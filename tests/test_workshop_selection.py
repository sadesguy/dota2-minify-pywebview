import os
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
    from unittest.mock import Mock

    from ui import Api

    if os.name == "nt":
        pytest.skip("Mac bootstrap uses fcntl")
    from core import macos_app

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
    assert api.download_workshop_tools()
    setup.assert_called_once_with(base.base_dir, base.mac_app_resources, gui=True)


def test_manual_download_still_offers_dialog_outside_packaged_mac(monkeypatch):
    from ui import Api

    api = Api()
    monkeypatch.delenv("MINIFY_WORKSHOP_READY", raising=False)
    monkeypatch.setattr(api, "get_state", lambda _: True)
    assert not api.should_offer_workshop_download()
    assert api.should_offer_workshop_download(manual=True)
