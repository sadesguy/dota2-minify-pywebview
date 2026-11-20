import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS bootstrap", allow_module_level=True)

from core import macos_app


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    # The shared test setup stubs filesystem discovery for Steam. Use real files here.
    from conftest import original_exists

    monkeypatch.setattr(os.path, "exists", original_exists)
    resources = tmp_path / "My App.app" / "Contents" / "Resources"
    defaults = resources / "defaults"
    for root in ("mods", "plugins", "themes"):
        (defaults / root).mkdir(parents=True)
    for name in ("mods/keep", "mods/deleted", "themes/update", "README.md", "LICENSE"):
        (defaults / name).write_text("stock")
    (resources / "macos").mkdir()
    work = tmp_path / "App Support" / "Workspace"
    mask = os.umask(0o077)
    with patch.dict(os.environ):
        yield work, resources
    os.umask(mask)


def test_finder_seed_and_upgrade(packaged):
    work, resources = packaged
    defaults = resources / "defaults"
    (defaults / "mods/.DS_Store").touch()
    (defaults / "mods/__MACOSX").mkdir()
    (defaults / "mods/__MACOSX/test").touch()
    args = ["Minify", "--skip-setup", "--help"]
    macos_app.prepare(work, resources, args)
    assert args == ["Minify", "--help"]
    assert sorted(x.name for x in (work / "mods").iterdir()) == ["deleted", "keep"]
    settings = work / "config/minify_config.json"
    assert json.loads(settings.read_text()) == {"fix_options": False}
    settings.write_text('{"mine":42}')
    (work / "mods/keep").write_text("user edit")
    (work / "mods/deleted").unlink()
    (work / "mods/new").write_text("user file")

    moved = resources.parents[1].with_name("Moved.app")
    resources.parents[1].rename(moved)
    resources = moved / "Contents/Resources"
    for name in ("mods/keep", "mods/deleted", "mods/new", "themes/update"):
        (resources / "defaults" / name).write_text("upgrade")
    macos_app.prepare(work, resources, ["Minify", "--version"])
    assert json.loads(settings.read_text()) == {"mine": 42}
    assert (work / "themes/update").read_text() == "upgrade"
    assert (work / "mods/keep").read_text() == "user edit"
    assert (work / "mods/new").read_text() == "user file"
    assert not (work / "mods/deleted").exists()
    assert not (work / "_internal").exists() and not (work / "Minify").exists()


def test_seed_rejects_symlinks(packaged, tmp_path):
    work, resources = packaged
    outside = tmp_path / "outside"
    outside.mkdir()
    work.mkdir(parents=True)
    (work / "mods").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        macos_app.prepare(work, resources, ["Minify", "--version"])
    assert list(outside.iterdir()) == []
    (work / "mods").unlink()
    (resources / "defaults/mods/link").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        macos_app.prepare(work, resources, ["Minify", "--version"])


def test_setup_environment_and_failure(packaged, monkeypatch):
    work, resources = packaged
    macos_app.prepare(work, resources, ["Minify", "--help"])
    driver = resources / "macos/setup-workshop"
    driver.write_text("#!/bin/sh\nprintf '%s\\0' '/Wine App/wine' '/test/prefix' '/bin' '/compiler'\n")
    driver.chmod(0o755)
    monkeypatch.setenv("CX_BOTTLE", "stale")
    assert macos_app.setup_workshop(work, resources, gui=False)
    assert os.environ["MINIFY_WINE"] == "/Wine App/wine"
    assert os.environ["MINIFY_WORKSHOP_READY"] == "1" and "CX_BOTTLE" not in os.environ
    driver.write_text("#!/bin/sh\nexit 2\n")
    assert not macos_app.setup_workshop(work, resources, gui=False)
    assert os.environ["MINIFY_WORKSHOP_READY"] == "0" and "MINIFY_DXVK_COMPILER" not in os.environ
    driver.write_text("#!/bin/sh\necho failed >&2; exit 1\n")
    with pytest.raises(SystemExit, match="Workshop setup failed"):
        macos_app.setup_workshop(work, resources, gui=False)

    run = subprocess.run

    def stub_alert(command, **kwargs):
        if command[0] != "/usr/bin/osascript":
            assert kwargs["env"]["MINIFY_SETUP_GUI"] == "1"
            return run(command, **kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", stub_alert)
    assert not macos_app.setup_workshop(work, resources, gui=True)
    assert next(work.glob("workshop-setup-*.log")).read_bytes() == b"failed\n"


def test_portable_command_forwards_args(tmp_path):
    from shutil import copy

    launcher = tmp_path / "Start Minify.command"
    copy(Path(__file__).resolve().parents[1] / "scripts/macos" / launcher.name, launcher)
    binary = tmp_path / "Minify"
    binary.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    binary.chmod(0o755)
    assert subprocess.check_output([launcher, "--mods", "a file with spaces"]).splitlines() == [
        b"--mods",
        b"a file with spaces",
    ]


def test_managed_startup_leaves_tools_and_applied_paks_alone(packaged, tmp_path, monkeypatch):
    work, resources = packaged
    macos_app.prepare(work, resources, ["Minify", "--version"])
    monkeypatch.chdir(work)
    (work / "logs").mkdir()
    (work / "cache").mkdir()
    old = work / "bin/rescomproot/local-files"
    partial = work / "config/rescomproot/local-files"
    for file in (old, partial):
        file.parent.mkdir(parents=True)
        file.write_text("keep")
    game = tmp_path / "game"
    game.mkdir()
    pak = game / "pak65_dir.vpk"
    pak.write_text("old Minify VPK")
    from core import base, config

    steam = tmp_path / "steam"
    executable = steam / base.DOTA_EXECUTABLE_PATH
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(base, "FROZEN", True)
    monkeypatch.setattr(base, "STEAM_DEFAULT_INSTALLATION_PATH", str(steam))
    settings = {"steam_root": str(steam), "steam_library": str(steam), "output_path": str(game)}
    monkeypatch.setattr(config, "get", settings.get)
    from core import migrations
    from patch import vpk_utils

    monkeypatch.setattr(vpk_utils, "is_minify_pak", lambda path: path == str(pak))
    for ready in ("0", "1"):
        monkeypatch.setenv("MINIFY_WORKSHOP_READY", ready)
        migrations.Migrations()
        assert all(file.read_text() == "keep" for file in (old, partial))
        assert pak.read_text() == "old Minify VPK"
