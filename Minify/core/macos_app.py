"""First launch of the macOS distributions. Source and other platforms do not use this module."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def _no_symlink(path):
    if path.is_symlink():
        raise ValueError(f"Refusing symlink: {path}")
    return path


def _sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _seed(defaults, work):
    record = _no_symlink(work / ".minify-stock.json")
    if record.exists() and not record.is_file():
        raise ValueError(f"Expected a regular file: {record}")
    stock = json.loads(record.read_bytes()) if record.exists() else {}
    if not isinstance(stock, dict):
        raise ValueError(f"Invalid stock file: {record}")

    with tempfile.TemporaryDirectory(dir=work) as stage:
        tmp = Path(stage, "next")
        changed = False

        def install(src):
            nonlocal changed
            if (
                src.name.startswith(".")
                or src.name in {"__MACOSX", "__pycache__", "node_modules"}
                or src.suffix in {".pyc", ".pyo"}
            ):
                return
            rel = src.relative_to(defaults)
            dest = _no_symlink(work / rel)
            if _no_symlink(src).is_dir():
                dest.mkdir(exist_ok=True)
                for child in src.iterdir():
                    install(child)
                return
            if not src.is_file() or (dest.exists() and not dest.is_file()):
                raise ValueError(f"Expected regular file: {rel}")
            new = _sha256(src)
            old = stock.get(rel.as_posix())
            if dest.exists():
                if old is None or old == new or _sha256(dest) != old:
                    return
            elif old is not None:
                return  # Keep a stock file the user deleted.
            shutil.copyfile(src, tmp)
            if old is None:
                os.link(tmp, dest)  # Never overwrite a new user file.
                tmp.unlink()
            else:
                tmp.replace(dest)
            stock[rel.as_posix()] = new
            changed = True

        try:
            for name in ("mods", "plugins", "themes", "README.md", "LICENSE"):
                install(defaults / name)
        finally:
            if changed:
                tmp.write_text(json.dumps(stock, sort_keys=True), encoding="utf-8")
                tmp.replace(record)


def _initialize(work, resources):
    if resources:
        _no_symlink(work.parent)
        _no_symlink(work).mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Serialize a pair of Finder launches, including upgrades of stock files.
        fcntl.flock(fd, fcntl.LOCK_EX)
        if resources:
            _seed(_no_symlink(resources / "defaults"), work)
        config = _no_symlink(work / "config")
        config.mkdir(mode=0o700, exist_ok=True)
        settings = _no_symlink(config / "minify_config.json")
        if not settings.exists():
            with settings.open("x", encoding="utf-8") as file:
                json.dump({"fix_options": False}, file)
    finally:
        os.close(fd)


def is_packaged(work_dir, resources_dir):
    return bool(resources_dir) or (Path(work_dir) / "setup-workshop").is_file()


def setup_workshop(work_dir, resources_dir, *, gui):
    work = Path(work_dir)
    driver = (Path(resources_dir) / "macos" if resources_dir else work) / "setup-workshop"
    os.environ["MINIFY_WORKSHOP_READY"] = "0"
    os.environ.pop("MINIFY_DXVK_COMPILER", None)
    env = os.environ.copy()
    if gui:
        env["MINIFY_SETUP_GUI"] = "1"
    else:
        env.pop("MINIFY_SETUP_GUI", None)
    try:
        proc = subprocess.run(
            [driver], cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE if gui else None
        )
        if proc.returncode == 2:  # User declined a download.
            return False
        proc.check_returncode()
        wine, prefix, path, dxvk, end = map(os.fsdecode, proc.stdout.split(b"\0"))
        if end or not wine or not prefix or not path:
            raise ValueError("Invalid environment from Workshop setup")
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        if not gui:
            raise SystemExit(f"Workshop setup failed: {error}") from error
        fd, log = tempfile.mkstemp(prefix="workshop-setup-", suffix=".log", dir=work)
        with os.fdopen(fd, "wb") as file:
            file.write(getattr(error, "stderr", None) or str(error).encode())
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                'on run argv\ndisplay alert "Workshop setup failed" message (item 1 of argv)\nend run',
                f"Workshop Tools are disabled. Setup log: {log}",
            ],
            check=False,
        )
        return False
    for key in (
        "CX_ROOT",
        "CX_HOME",
        "CX_BOTTLE",
        "WINEARCH",
        "WINELOADER",
        "WINESERVER",
        "WINEDLLPATH",
        "WINEMSYNC",
        "CX_APPLEGPTK_LIBD3DSHARED_PATH",
    ):
        os.environ.pop(key, None)
    os.environ.update(MINIFY_WINE=wine, WINEPREFIX=prefix, PATH=path, MINIFY_WORKSHOP_READY="1")
    if dxvk:
        os.environ["MINIFY_DXVK_COMPILER"] = dxvk
    return True


def prepare(work_dir, resources_dir, argv):
    work = Path(work_dir)
    resources = Path(resources_dir) if resources_dir else None
    if not is_packaged(work, resources):
        return  # Unpackaged PyInstaller executable.
    if resources:
        os.umask(0o077)
    _initialize(work, resources)
    os.environ["MINIFY_WORKSHOP_READY"] = "0"
    os.environ.pop("MINIFY_DXVK_COMPILER", None)
    if argv[1:2] == ["--skip-setup"]:
        del argv[1]
        return
    if argv[1:2] in (["--version"], ["-v"], ["--help"], ["-h"], ["config"], ["mods"], ["uninstall"]):
        return
    gui = argv[1:] in ([], ["--host"]) and not (sys.stdin and sys.stdin.isatty())
    setup_workshop(work, resources, gui=gui)
