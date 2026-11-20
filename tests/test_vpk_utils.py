import hashlib
import json
import os
import struct
from unittest.mock import patch

import pytest
import vpk
from patch import vpk_utils


@pytest.mark.parametrize(
    ("saved", "selected", "expected"),
    [
        (None, ["Explicit"], {"Explicit": True}),
        ({"Saved": True}, ["Explicit"], {"Explicit": True}),
        ({"Saved": True, "Off": False}, None, {"Saved": True, "Off": False}),
        (None, None, {}),
    ],
)
def test_metadata_uses_effective_selection_without_changing_saved_mods(
    tmp_path, monkeypatch, saved, selected, expected
):
    saved_file = tmp_path / "mods.json"
    if saved is not None:
        saved_file.write_text(json.dumps(saved))
    monkeypatch.setattr(vpk_utils.base, "mods_config_dir", str(saved_file))
    monkeypatch.setattr(vpk_utils.constants, "dota_steam_inf_path", str(tmp_path / "steam.inf"))
    output = tmp_path / "output"
    vpk_utils.dump_metadata(str(output), selected_mods=selected)
    assert json.loads((output / "minify_mods.json").read_bytes()) == expected
    assert (saved_file.read_text() if saved_file.is_file() else None) == (
        json.dumps(saved) if saved is not None else None
    )


def test_dump_metadata_default_behavior(tmp_path):
    target_dir = str(tmp_path / "output")
    os.makedirs(target_dir, exist_ok=True)

    # Mock config.get to return False for opt_out_vpk_metadata
    def mock_get(key, default=None):
        if key == "opt_out_vpk_metadata":
            return False
        return default

    with patch("core.config.get", side_effect=mock_get):
        # Mock shutil.copy and utils.open_utf8 to avoid hitting real steam.inf or mods.json
        with patch("shutil.copy") as _, patch("patch.vpk_utils.utils.open_utf8") as mock_open:
            vpk_utils.dump_metadata(target_dir, mod_name="test_mod")

            # Since mod_name is provided, it should create {mod_name}.txt
            assert os.path.exists(os.path.join(target_dir, "test_mod.txt"))

            # Should have called open_utf8 to write minify_version.txt
            mock_open.assert_any_call(os.path.join(target_dir, "minify_version.txt"), "w")


def test_dump_metadata_opt_out(tmp_path):
    target_dir = str(tmp_path / "output")
    os.makedirs(target_dir, exist_ok=True)

    # Mock config.get to return True for opt_out_vpk_metadata
    def mock_get(key, default=None):
        if key == "opt_out_vpk_metadata":
            return True
        return default

    with patch("core.config.get", side_effect=mock_get):
        with patch("shutil.copy") as mock_copy, patch("patch.vpk_utils.utils.open_utf8") as mock_open:
            vpk_utils.dump_metadata(target_dir, mod_name="test_mod")

            # Since we opted out, it should return early:
            # {mod_name}.txt should NOT be created, and copy/open should NOT be called.
            assert not os.path.exists(os.path.join(target_dir, "test_mod.txt"))
            mock_copy.assert_not_called()
            mock_open.assert_not_called()


def test_is_minify_pak_detects_metadata_marker(tmp_path):
    pak_dir = tmp_path / "pak"
    pak_dir.mkdir()
    (pak_dir / "minify_version.txt").write_text("1.0.0")
    pak_path = str(tmp_path / "test_dir.vpk")
    vpk_utils.pack(str(pak_dir), pak_path)

    assert vpk_utils.is_minify_pak(pak_path) is True


def test_is_minify_pak_rejects_foreign_pak(tmp_path):
    pak_dir = tmp_path / "pak"
    pak_dir.mkdir()
    (pak_dir / "random.txt").write_text("test")
    pak_path = str(tmp_path / "test_dir.vpk")
    vpk_utils.pack(str(pak_dir), pak_path)

    assert vpk_utils.is_minify_pak(pak_path) is False


def test_is_minify_pak_missing_file(tmp_path):
    assert vpk_utils.is_minify_pak(str(tmp_path / "missing_dir.vpk")) is False


def test_is_minify_pak_rejects_non_vpk_file(tmp_path):
    invalid_path = tmp_path / "fake_dir.vpk"
    invalid_path.write_text("not a vpk")

    assert vpk_utils.is_minify_pak(str(invalid_path)) is False


def test_pack_chunked_and_read(tmp_path):
    source_dir = tmp_path / "source"
    (source_dir / "subdir").mkdir(parents=True)
    (source_dir / "root_file.txt").write_text("Root file content")
    (source_dir / "subdir" / "nested.txt").write_text("Nested file content " * 50)
    (source_dir / "subdir" / "other.bin").write_bytes(b"\x01\x02\x03\x04" * 100)

    out_dir = tmp_path / "out"
    dir_vpk_path = str(out_dir / "pak66_dir.vpk")

    # Use a small chunk size of 300 bytes so multiple chunks are generated
    vpk_utils.pack(str(source_dir), dir_vpk_path, chunk_size=300)

    # Check chunk files exist
    chunk_0 = out_dir / "pak66_000.vpk"
    chunk_1 = out_dir / "pak66_001.vpk"
    assert os.path.exists(dir_vpk_path)
    assert chunk_0.exists()
    assert chunk_1.exists()

    # Validate the VPK v2 checksums against the generated external archives.
    with open(dir_vpk_path, "rb") as f:
        header = struct.unpack("<7I", f.read(28))
        _, _, tree_length, file_data_length, archive_md5_length, other_md5_length, _ = header
        tree = f.read(tree_length)
        archive_md5_entries = f.read(archive_md5_length)
        checksums = f.read(other_md5_length)

    assert archive_md5_length > 0
    assert archive_md5_length % 28 == 0
    assert hashlib.md5(tree).digest() == checksums[:16]
    assert hashlib.md5(archive_md5_entries).digest() == checksums[16:32]
    with open(dir_vpk_path, "rb") as f:
        whole_file_data = f.read(28 + tree_length + file_data_length + archive_md5_length + 32)
    assert hashlib.md5(whole_file_data).digest() == checksums[32:48]

    archive_coverage = {}
    for offset in range(0, archive_md5_length, 28):
        archive_idx, hash_type, chunk_offset, length, checksum = struct.unpack_from(
            "<HHII16s", archive_md5_entries, offset
        )
        assert hash_type == 0
        chunk_path = out_dir / f"pak66_{archive_idx:03d}.vpk"
        with open(chunk_path, "rb") as chunk_file:
            chunk_file.seek(chunk_offset)
            assert hashlib.md5(chunk_file.read(length)).digest() == checksum
        archive_coverage.setdefault(archive_idx, []).append((chunk_offset, length))

    for archive_idx, ranges in archive_coverage.items():
        chunk_size = os.path.getsize(out_dir / f"pak66_{archive_idx:03d}.vpk")
        assert ranges[0][0] == 0
        assert sum(length for _, length in ranges) == chunk_size

    # Read back with standard vpk reader
    pak = vpk.open(dir_vpk_path)
    assert pak.get_file("root_file.txt").read().decode("utf-8") == "Root file content"
    assert pak.get_file("subdir/nested.txt").read().decode("utf-8") == "Nested file content " * 50
    assert pak.get_file("subdir/other.bin").read() == b"\x01\x02\x03\x04" * 100


def test_pack_non_chunked_and_read(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "file.txt").write_text("Single archive content")
    (source_dir / "second.txt").write_text("Another file")

    out_dir = tmp_path / "out"
    dir_vpk_path = str(out_dir / "pak66_dir.vpk")

    vpk_utils.pack(str(source_dir), dir_vpk_path, chunk_size=1)
    assert (out_dir / "pak66_001.vpk").exists()

    vpk_utils.pack(str(source_dir), dir_vpk_path, chunk_size=None)

    assert sorted(path.name for path in out_dir.iterdir()) == ["pak66_dir.vpk"]

    with open(dir_vpk_path, "rb") as f:
        header = struct.unpack("<7I", f.read(28))
    assert header[3] == len("Single archive content") + len("Another file")

    pak = vpk.open(dir_vpk_path)
    assert pak["file.txt"].archive_index == 0x7FFF
    assert pak.verify()
    assert pak.get_file("file.txt").read().decode("utf-8") == "Single archive content"
    assert pak.get_file("second.txt").read().decode("utf-8") == "Another file"


@pytest.mark.parametrize("chunk_size", [None, 8], ids=["embedded", "chunked"])
def test_pack_skips_macos_metadata_without_losing_assets(tmp_path, chunk_size):
    source = tmp_path / "source"
    assets = {
        "models/hero.vmdl_c": b"model bytes",
        "particles/effect.vpcf_c": b"particle bytes",
        ".example": b"hidden file",
        "LICENSE": b"extensionless file",
    }
    if os.name != "nt":
        assets["trailing."] = b"trailing dot"
    junk = [".DS_Store", "particles/.ds_store", "models/._hero.vmdl_c", "nested/__MACOSX/deeper/preview.png"]
    for name, data in (assets | dict.fromkeys(junk, b"metadata")).items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    index = str(tmp_path / "pak66_dir.vpk")
    vpk_utils.pack(str(source), index, chunk_size=chunk_size)
    pak = vpk.open(index)
    # vpk 1.4 exposes the format's single-space extension sentinel as a literal ". " suffix.
    names = {name.removesuffix(". "): name for name in pak}
    assert names.keys() == assets.keys()
    for name, data in assets.items():
        with pak[names[name]] as entry:
            assert entry.verify()
            assert entry.read() == data
            assert (entry.archive_index == 0x7FFF) == (chunk_size is None)


def test_pack_cleans_stale_chunks(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "file1.txt").write_text("A" * 500)
    (source_dir / "file2.txt").write_text("B" * 500)

    out_dir = tmp_path / "out"
    dir_vpk_path = str(out_dir / "pak66_dir.vpk")

    # First pack with 300 byte chunks creates multiple chunks
    vpk_utils.pack(str(source_dir), dir_vpk_path, chunk_size=300)
    assert (out_dir / "pak66_001.vpk").exists()
    neighbors = [out_dir / "pak66_001_backup.vpk", out_dir / "pak66_999.vpk"]
    for neighbor in neighbors:
        neighbor.write_bytes(b"keep")

    # Second pack with 10MB chunk fits in a single chunk (_000.vpk)
    vpk_utils.pack(str(source_dir), dir_vpk_path, chunk_size=10 * 1024 * 1024)
    assert (out_dir / "pak66_000.vpk").exists()
    assert not (out_dir / "pak66_001.vpk").exists()
    assert all(neighbor.read_bytes() == b"keep" for neighbor in neighbors)


def test_pack_bad_index_preserves_previous_pak_and_chunks(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"old a")
    (source / "b.txt").write_bytes(b"old b")
    output = tmp_path / "out"
    index = str(output / "pak66_dir.vpk")
    vpk_utils.pack(str(source), index, chunk_size=5)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    assert set(before) == {"pak66_dir.vpk", "pak66_000.vpk", "pak66_001.vpk"}

    (source / "a.txt").write_bytes(b"new content")
    with patch.object(vpk_utils.vpk, "open", side_effect=ValueError("unreadable index")), pytest.raises(ValueError):
        vpk_utils.pack(str(source), index, chunk_size=5)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before

    with pytest.raises(FileNotFoundError):
        vpk_utils.pack(str(tmp_path / "missing"), index)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_pack_does_not_overwrite_unclaimed_chunks(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "asset.txt").write_bytes(b"new")
    index = tmp_path / "pak66_dir.vpk"
    chunk = tmp_path / "pak66_000.vpk"
    index.write_bytes(b"broken old index")
    chunk.write_bytes(b"unknown owner")
    with pytest.raises(FileExistsError, match="unclaimed VPK chunk"):
        vpk_utils.pack(source, index)
    assert index.read_bytes() == b"broken old index" and chunk.read_bytes() == b"unknown owner"
    vpk_utils.pack(source, index, chunk_size=None)
    with vpk.open(str(index))["asset.txt"] as entry:
        assert entry.read() == b"new"
    assert chunk.read_bytes() == b"unknown owner"


def test_pack_standalone_leaves_sibling_packages_untouched(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "map.vmap_c").write_bytes(b"compiled map")
    output = tmp_path / "maps"
    output.mkdir()
    siblings = {"dota_000.vpk": b"unrelated chunk", "dota_other.vpk": b"unrelated pak"}
    for name, data in siblings.items():
        (output / name).write_bytes(data)

    index = str(output / "dota.vpk")
    vpk_utils.pack(str(source), index, chunk_size=None)
    with vpk.open(index)["map.vmap_c"] as entry:
        assert entry.read() == b"compiled map"
    assert {name: (output / name).read_bytes() for name in siblings} == siblings


def test_uninstall_preserves_other_paks_and_maps(tmp_path, monkeypatch):
    from patch import unins

    source = tmp_path / "source"
    source.mkdir()
    (source / "minify_version.txt").write_text("2")
    language = tmp_path / "language"
    maps = language / "maps"
    maps.mkdir(parents=True)
    vpk_utils.pack(source, language / "pak66_dir.vpk")
    vpk_utils.pack(source, maps / "dota.vpk", chunk_size=None)
    other = (maps / "my_map.vpk", language / "pak66_backup.vpk", language / "pak66_111.vpk")
    for neighbor in other:
        neighbor.write_bytes(b"keep")
    monkeypatch.setattr(unins.constants, "minify_dota_possible_language_output_paths", [str(language)])
    monkeypatch.setattr(unins.steam, "remove_minify_lang", lambda: None)
    monkeypatch.setattr(unins.steam, "restore_boot_language", lambda: None)
    monkeypatch.setattr(unins.registry, "get_plugins", lambda: [])
    monkeypatch.setattr(unins.helper, "bulk_exec_script", lambda *_: None)
    unins.uninstall()
    assert not (maps / "dota.vpk").exists()
    assert not (language / "pak66_dir.vpk").exists()
    assert not (language / "pak66_000.vpk").exists()
    assert all(neighbor.read_bytes() == b"keep" for neighbor in other)
