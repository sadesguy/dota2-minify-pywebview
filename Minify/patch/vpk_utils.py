import binascii
import hashlib
import os
import shutil
import struct
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import vpk
from core import base, config, constants, fs, log, output, utils

DEFAULT_VPK_CHUNK_SIZE = 100 * 1024 * 1024
VPK_ARCHIVE_MD5_CHUNK_SIZE = 1024 * 1024


def pack(source_dir, output_vpk_dir_path, chunk_size: int | None = DEFAULT_VPK_CHUNK_SIZE):
    """
    Packs source_dir into a VPK v2 archive (e.g. pak66_dir.vpk + pak66_000.vpk, ...).
    Each external archive is capped at chunk_size bytes. When chunk_size is None,
    file data is embedded in the directory VPK.
    """
    index = Path(output_vpk_dir_path)
    if chunk_size is not None and not index.name.endswith("_dir.vpk"):
        raise ValueError("A chunked VPK needs a name ending in _dir.vpk")
    index.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=index.parent) as tmp:
        staged = Path(tmp) / index.name
        _write_vpk(source_dir, staged, chunk_size)
        chunks = {p.name for p in staged.parent.iterdir() if p != staged}
        previous = chunk_names(index)
        for name in chunks - previous:
            if os.path.lexists(index.with_name(name)):
                raise FileExistsError(f"Move the unclaimed VPK chunk aside before packing: {index.with_name(name)}")
        for name in chunks:
            staged.with_name(name).replace(index.with_name(name))
        staged.replace(index)

    for name in previous - chunks:
        index.with_name(name).unlink(missing_ok=True)


def chunk_names(index: Path) -> set[str]:
    if not index.name.endswith("_dir.vpk"):
        return set()
    try:
        pak = vpk.open(str(index), read_header_only=False)
        numbers = {pak.get_file_meta(name)["archive_index"] for name in pak}
    except (FileNotFoundError, ValueError, struct.error, AssertionError):
        # A missing or broken index cannot identify which neighboring chunks it owns.
        return set()
    return {f"{index.stem.removesuffix('_dir')}_{number:03d}.vpk" for number in numbers if number != 0x7FFF}


def _macos_metadata(name):
    return name.lower() in {".ds_store", "__macosx"} or name.startswith("._")


def _write_vpk(source_dir, output_vpk_dir_path, chunk_size):
    base_name = os.path.splitext(output_vpk_dir_path)[0]
    chunk_prefix = base_name.removesuffix("_dir")

    def fail_on_unreadable_directory(error):
        raise error  # os.walk otherwise silently skips inaccessible or missing input.

    # VPK directory order: extension, directory, filename.
    tree = {}
    for root, dirs, filenames in os.walk(source_dir, onerror=fail_on_unreadable_directory):
        dirs[:] = sorted(d for d in dirs if not _macos_metadata(d))
        rel = os.path.relpath(root, source_dir).replace("\\", "/")
        if rel == ".":
            rel = " "

        for filename in sorted(filenames):
            if _macos_metadata(filename):
                continue
            name, suffix = os.path.splitext(filename)
            ext = suffix[1:] or " "  # An empty extension would terminate the VPK index.
            if suffix == ".":
                name = filename
            tree.setdefault(ext, {}).setdefault(rel, []).append((name, os.path.join(root, filename)))

    # Calculate tree length in bytes
    tree_length = 0
    for ext, relpaths in tree.items():
        tree_length += len(ext.encode("utf-8")) + 2
        for relpath, file_entries in relpaths.items():
            tree_length += len(relpath.encode("utf-8")) + 2
            for name, _ in file_entries:
                tree_length += len(name.encode("utf-8")) + 1 + 18
    tree_length += 1

    # Write chunk data files
    file_entries_meta = {}  # abs_path -> (crc, archive_idx, offset, length)
    current_chunk_idx = 0
    current_chunk_size = 0
    current_chunk_file = None
    embedded_data_offset = 28 + tree_length
    embedded = chunk_size is None or not tree

    def open_chunk_file(idx):
        return open(f"{chunk_prefix}_{idx:03d}.vpk", "wb")

    try:
        if embedded:
            current_chunk_idx = 0x7FFF
            current_chunk_file = open(output_vpk_dir_path, "w+b")
            current_chunk_file.seek(embedded_data_offset)
        else:
            current_chunk_file = open_chunk_file(current_chunk_idx)
        for relpaths in tree.values():
            for file_entries in relpaths.values():
                for _, src_path in file_entries:
                    file_size = os.path.getsize(src_path)
                    if (
                        chunk_size is not None
                        and current_chunk_size > 0
                        and current_chunk_size + file_size > chunk_size
                    ):
                        current_chunk_file.close()
                        current_chunk_idx += 1
                        current_chunk_size = 0
                        current_chunk_file = open_chunk_file(current_chunk_idx)

                    crc = 0
                    file_offset = (
                        current_chunk_file.tell() - embedded_data_offset if embedded else current_chunk_file.tell()
                    )
                    with open(src_path, "rb") as f:
                        while chunk := f.read(65536):
                            crc = binascii.crc32(chunk, crc)
                            current_chunk_file.write(chunk)

                    current_chunk_size += file_size
                    file_entries_meta[src_path] = (crc & 0xFFFFFFFF, current_chunk_idx, file_offset, file_size)
    finally:
        if current_chunk_file:
            current_chunk_file.close()

    archive_md5_entries = bytearray()
    if not embedded:
        for archive_idx in range(current_chunk_idx + 1):
            archive_path = f"{chunk_prefix}_{archive_idx:03d}.vpk"
            archive_size = os.path.getsize(archive_path)
            with open(archive_path, "rb") as archive_file:
                for offset in range(0, archive_size, VPK_ARCHIVE_MD5_CHUNK_SIZE):
                    data = archive_file.read(VPK_ARCHIVE_MD5_CHUNK_SIZE)
                    archive_md5_entries.extend(
                        struct.pack(
                            "HHII16s",
                            archive_idx,
                            0,
                            offset,
                            len(data),
                            hashlib.md5(data).digest(),
                        )
                    )

    # Write _dir.vpk directory file
    with open(output_vpk_dir_path, "r+b" if embedded else "w+b") as f:
        # VPK2 header: signature (0x55aa1234), version (2), tree_length,
        # embed_chunk_len, archive_md5_len, self_hashes_len (48), sig_len (0)
        embed_chunk_length = current_chunk_size if embedded else 0
        f.write(struct.pack("3I4I", 0x55AA1234, 2, tree_length, embed_chunk_length, len(archive_md5_entries), 48, 0))
        header_len = f.tell()

        tree_bytes = bytearray()
        for ext, relpaths in tree.items():
            tree_bytes.extend(ext.encode("utf-8") + b"\x00")
            for relpath, file_entries in relpaths.items():
                tree_bytes.extend(relpath.encode("utf-8") + b"\x00")
                for name, src_path in file_entries:
                    tree_bytes.extend(name.encode("utf-8") + b"\x00")
                    crc, archive_idx, offset, length = file_entries_meta[src_path]
                    tree_bytes.extend(struct.pack("IHHIIH", crc, 0, archive_idx, offset, length, 0xFFFF))
                tree_bytes.extend(b"\x00")
            tree_bytes.extend(b"\x00")
        tree_bytes.extend(b"\x00")

        assert len(tree_bytes) == tree_length, f"Tree length mismatch: expected {tree_length}, got {len(tree_bytes)}"
        f.write(tree_bytes)
        if embedded:
            f.seek(header_len + tree_length + embed_chunk_length)
        f.write(archive_md5_entries)

        # Hashes: tree_checksum, archive_md5_entries_checksum, file_checksum
        tree_checksum = hashlib.md5(tree_bytes).digest()
        archive_md5_checksum = hashlib.md5(archive_md5_entries).digest()

        file_checksum = hashlib.md5()
        f.seek(0)
        data_length = header_len + tree_length + embed_chunk_length + len(archive_md5_entries)
        file_checksum.update(f.read(data_length))
        file_checksum.update(tree_checksum)
        file_checksum.update(archive_md5_checksum)

        f.seek(data_length)
        f.write(tree_checksum)
        f.write(archive_md5_checksum)
        f.write(file_checksum.digest())

    # Checksums alone can pass even when the directory tree cannot be read.
    if len(vpk.open(output_vpk_dir_path)) != len(file_entries_meta):
        raise ValueError(f"VPK index does not list every packed file: {output_vpk_dir_path}")


def extract(vpk_to_extract_from, paths, path_to_extract_to=base.build_dir):
    if isinstance(paths, str):
        paths = [paths]

    vpk_lock = threading.Lock()

    def extract_file(path):
        if not os.path.exists(full_path := os.path.join(path_to_extract_to, path)):  # extract files from VPK only once
            output.add_text("&extracting_terminal", path, indent=True)
            with vpk_lock:
                pakfile = vpk_to_extract_from.get_file(path)

            if pakfile:
                fs.create_dirs(os.path.dirname(full_path))
                pakfile.save(full_path)
            else:
                log.write_warning(f"File not found in VPK: {path}")

    with ThreadPoolExecutor() as executor:
        executor.map(extract_file, paths)


def dump(vpk_obj, output_dir, check_exists=True):
    for filepath in vpk_obj:
        # Sanitize filepath to prevent invalid characters or quotes
        clean_path = filepath.strip().strip('"').strip("'").replace("\\", "/").lstrip("/")
        if not clean_path:
            continue

        full_path = os.path.join(output_dir, clean_path)
        if check_exists and os.path.exists(full_path):
            continue

        fs.create_dirs(os.path.dirname(full_path))
        vpk_obj.get_file(filepath).save(full_path)


def is_minify_pak(vpk_path):
    """
    Determines whether a VPK file was generated by Minify by checking for the
    metadata marker files that dump_metadata() packs into created Paks.
    """
    marker_files = ["minify_mods.json", "minify_vpk_mods.txt", "minify_version.txt"]

    try:
        pak = vpk.open(vpk_path)
        pak_files = set(pak)
    except Exception:
        return False
    return not pak_files.isdisjoint(marker_files)


def dump_metadata(target_dir, mod_name=None, vpk_mods=None, extra_lists=None, *, selected_mods=None):
    """
    Standardizes metadata files for generated Paks.
    - target_dir: Where to dump.
    - mod_name: If provided, creates {mod_name}.txt (for single mod patches).
    - vpk_mods: List of VPK mod names for minify_vpk_mods.txt.
    - extra_lists: Dict of {filename: [lines]} for additional metadata files.
    """
    if config.get("opt_out_vpk_metadata"):
        return

    fs.create_dirs(target_dir)
    # 1. Base Info
    if mod_name is None:
        states = (
            config.read_json_file(base.mods_config_dir) if selected_mods is None else dict.fromkeys(selected_mods, True)
        )
        config.write_json_file(os.path.join(target_dir, "minify_mods.json"), states)
    else:
        open(os.path.join(target_dir, f"{mod_name}.txt"), "w").close()

    # 2. Lists
    if vpk_mods:
        with utils.open_utf8(os.path.join(target_dir, "minify_vpk_mods.txt"), "w") as f:
            f.write("\n".join(vpk_mods))

    if extra_lists:
        for filename, lines in extra_lists.items():
            with utils.open_utf8(os.path.join(target_dir, filename), "w") as f:
                f.write("\n".join(lines))

    # 3. Minify Version
    with utils.open_utf8(os.path.join(target_dir, "minify_version.txt"), "w") as f:
        f.write(base.VERSION)

    # 4. Dota Version (steam.inf)
    if os.path.exists(constants.dota_steam_inf_path):
        shutil.copy(constants.dota_steam_inf_path, os.path.join(target_dir, "steam.inf"))
