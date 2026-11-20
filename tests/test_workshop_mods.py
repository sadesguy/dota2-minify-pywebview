import json

import conditions
import helper
import patch
import vpk
from core import base, constants, mods_shared
from ui.services.mod_service import ModService


def test_skipping_workshop_keeps_choices_out_of_the_patch_not_out_of_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mods = ["compiler", "dependant", "ordinary", "optional"]
    for mod in mods:
        files = tmp_path / "mods" / mod / "files"
        files.mkdir(parents=True)
        (files / f"{mod}.txt").write_text(mod)
        (files.parent / "script_after_patch.py").write_text("")
    for mod in ("compiler", "optional"):
        (tmp_path / "mods" / mod / "styling.css").touch()
    (tmp_path / "mods/optional/manifest.json").write_text('{"skip_workshop_check": true}')
    selections = {mod: True for mod in mods}
    state = tmp_path / "states.json"
    state.write_text(json.dumps(selections))
    monkeypatch.setattr(base, "is_mac", True)
    monkeypatch.setattr(base, "mods_dir", str(tmp_path / "mods"))
    monkeypatch.setattr(base, "mods_config_dir", str(state))
    monkeypatch.setenv("MINIFY_WORKSHOP_READY", "0")
    monkeypatch.setattr(conditions, "workshop_installed", False)
    monkeypatch.setattr(constants, "mods_with_order", mods)
    monkeypatch.setattr(constants, "mod_dependencies_list", [{"dependant": ["compiler"]}])
    monkeypatch.setattr(constants, "mod_conflicts_list", [])
    monkeypatch.setattr(mods_shared, "visually_available_mods", mods)
    monkeypatch.setattr(mods_shared, "mods_with_order", mods)
    monkeypatch.setattr(mods_shared, "scan_mods", lambda: None)
    monkeypatch.setattr(mods_shared, "enforce_locale_mod_states", lambda: None)

    service = ModService()
    data = service.get_mods()
    assert {row["name"] for row in data if row["untickable"]} == {"compiler", "dependant"}
    assert {row["name"] for row in data if row["enabled"]} == {"ordinary", "optional"}
    # The UI sends the whole list whenever a user toggles one checkbox.
    service.set_mods({row["name"]: row["enabled"] for row in data})
    assert json.loads(state.read_text()) == selections

    monkeypatch.setattr(conditions, "is_dota_running", lambda *_: False)
    monkeypatch.setattr(conditions, "check_binaries", lambda: True)
    monkeypatch.setattr(constants, "dota_steam_inf_path", "game_version")
    monkeypatch.setattr(constants, "minify_dota_compile_input_path", "input")
    monkeypatch.setattr(constants, "minify_dota_compile_output_path", "compiled")
    monkeypatch.setattr(constants, "minify_dota_tools_required_path", "tools")
    monkeypatch.setattr(constants, "dota_game_pak_path", "game.vpk")
    (tmp_path / base.logs_dir).mkdir()
    (tmp_path / base.cache_dir).mkdir()
    (tmp_path / base.dota_steam_inf_cache).touch()
    (tmp_path / "empty").mkdir()
    patch.vpk_utils.pack("empty", "game.vpk", chunk_size=None)
    monkeypatch.setattr(helper, "output_path", str(tmp_path / "out"))
    monkeypatch.setattr(patch.registry, "get_plugins", lambda: [])
    calls = []
    monkeypatch.setattr(helper, "exec_script", lambda path, mod, order, **kw: calls.append((mod, order)))

    patch.patcher(mods_string="compiler")
    assert not (tmp_path / "out").exists()
    patch.patcher()
    index = tmp_path / "out/pak66_dir.vpk"
    assert index.is_file(), (tmp_path / base.log_crashlog).read_text()
    pak = vpk.open(str(index))
    assert {name for name in pak if name in {f"{mod}.txt" for mod in mods}} == {"ordinary.txt", "optional.txt"}
    assert {mod for mod, order in calls if order == "after_patch"} == {"ordinary", "optional"}
    assert json.loads(state.read_text()) == selections

    monkeypatch.setenv("MINIFY_WORKSHOP_READY", "1")
    monkeypatch.setattr(conditions, "workshop_installed", True)
    assert all(row["enabled"] for row in service.get_mods())
