import os
from unittest.mock import Mock

import pytest

import conditions
from core import base, constants


@pytest.mark.parametrize(
    ("mac", "ready", "selection", "enabled"),
    [
        (True, "1", "private", True),
        (True, "0", "private", False),
        (True, None, "steam", True),
        (False, None, "steam", True),
    ],
)
def test_use_compiler_that_setup_checked(tmp_path, monkeypatch, mac, ready, selection, enabled):
    private = tmp_path / "tools"
    private_exe = private / "game/bin/win64/resourcecompiler.exe"
    private_exe.parent.mkdir(parents=True)
    private_exe.touch()
    steam_exe = tmp_path / "steam-compiler.exe"
    steam_exe.touch()

    with monkeypatch.context() as m:
        m.setattr(base, "is_mac", mac)
        m.setattr(base, "rescomp_override_dir", str(private))
        m.setattr(constants, "game_resource_compiler_path", str(steam_exe))
        if ready is None:
            m.delenv("MINIFY_WORKSHOP_READY", raising=False)
        else:
            m.setenv("MINIFY_WORKSHOP_READY", ready)
        m.setattr(conditions, "workshop_installed", False)
        # Restore module globals populated by recalc_rescomp_dirs() after this test.
        for key in (
            "rescomp_override",
            "dota_resource_compiler_path",
            "minify_dota_compile_input_path",
            "minify_dota_compile_output_path",
        ):
            m.setattr(constants, key, getattr(constants, key))
        constants.recalc_rescomp_dirs()
        assert constants.dota_resource_compiler_path == str(private_exe if selection == "private" else steam_exe)
        assert conditions.is_compiler_found() is enabled
        if ready == "1":
            private_exe.unlink()
            constants.recalc_rescomp_dirs()
            assert not conditions.is_compiler_found()


def test_skip_offer_keeps_manual_verified_setup(monkeypatch, tmp_path):
    from ui import Api

    if os.name == "nt":
        pytest.skip("Mac bootstrap uses fcntl")
    from core import macos_app, output

    api = Api()
    monkeypatch.setattr(base, "is_mac", True)
    monkeypatch.setattr(base, "FROZEN", True)
    monkeypatch.setattr(base, "base_dir", str(tmp_path))
    monkeypatch.setattr(base, "mac_app_resources", str(tmp_path / "Resources"))
    monkeypatch.setenv("MINIFY_WORKSHOP_READY", "0")
    assert not api.should_offer_workshop_download()
    assert not api.should_offer_workshop_download(manual=True)
    setup = Mock(return_value=True)
    monkeypatch.setattr(macos_app, "setup_workshop", setup)
    monkeypatch.setattr(constants, "recalc_rescomp_dirs", Mock())
    monkeypatch.setattr(conditions, "is_compiler_found", Mock(return_value=True))
    monkeypatch.setattr(conditions, "check_binaries", lambda: False)
    prefetch = Mock()
    monkeypatch.setattr(conditions, "resolve_dependencies", prefetch)
    try:
        assert api.download_workshop_tools()
        setup.assert_called_once_with(base.base_dir, base.mac_app_resources, report=output.add_text)
        prefetch.assert_called_once_with()
    finally:
        api.close()


@pytest.mark.parametrize(
    ("mac", "ready", "cached", "fetch"),
    [
        (True, "1", False, True),
        (True, "1", True, False),
        (True, None, False, False),
        (False, "1", False, False),
    ],
)
def test_prefetch_missing_patch_tools_only_for_managed_mac_gui(monkeypatch, mac, ready, cached, fetch):
    from ui import Api

    monkeypatch.setattr(base, "is_mac", mac)
    if ready is None:
        monkeypatch.delenv("MINIFY_WORKSHOP_READY", raising=False)
    else:
        monkeypatch.setenv("MINIFY_WORKSHOP_READY", ready)
    monkeypatch.setattr(conditions, "check_binaries", lambda: cached)
    resolve = Mock()
    monkeypatch.setattr(conditions, "resolve_dependencies", resolve)
    api = Api()
    api.close()
    assert resolve.call_count == int(fetch)


def test_offline_regular_mod_setup_reports_failure_without_opening_anything(tmp_path, monkeypatch):
    from ui import Api

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(base, "is_mac", True)
    monkeypatch.setenv("MINIFY_WORKSHOP_READY", "0")
    monkeypatch.setattr(conditions, "workshop_installed", False)
    monkeypatch.setattr(conditions.shutil, "which", lambda _: None)
    monkeypatch.setattr(constants, "rg_executable", "rg")
    monkeypatch.setattr(constants, "s2v_executable", "Source2Viewer-CLI")
    (tmp_path / "Source2Viewer-CLI").touch()
    download = Mock(return_value=False)
    monkeypatch.setattr(conditions.fs, "download_file", download)
    monkeypatch.setattr(conditions.fs, "open_thing", pytest.fail)
    monkeypatch.setattr(conditions.webbrowser, "open", pytest.fail)
    monkeypatch.setattr(conditions.log, "write_crashlog", pytest.fail)
    api = Api()
    try:
        assert [call.args[0] for call in download.call_args_list] == [constants.rg_latest]
        assert (tmp_path / "Source2Viewer-CLI").is_file()
        assert any("retry on patch" in entry["text"] and entry["type"] == "error" for entry in api.get_logs())
    finally:
        api.close()


def test_manual_download_still_offers_dialog_outside_packaged_mac(monkeypatch):
    from ui import Api

    api = Api()
    monkeypatch.delenv("MINIFY_WORKSHOP_READY", raising=False)
    monkeypatch.setattr(api, "get_state", lambda _: True)
    assert not api.should_offer_workshop_download()
    assert api.should_offer_workshop_download(manual=True)
