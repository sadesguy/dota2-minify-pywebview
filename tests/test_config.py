import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from core import config


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # conftest replaces config.get and config.set for Steam discovery.
    spec = importlib.util.spec_from_file_location("settings_under_test", config.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.base, "main_config_file_dir", str(tmp_path / "minify_config.json"))
    monkeypatch.setattr(module.base, "mods_config_dir", str(tmp_path / "mods.json"))
    return module


def test_empty_config_and_jsonc_schema(settings, tmp_path, monkeypatch):
    config_path = tmp_path / "minify_config.json"
    config_path.touch()
    schema = tmp_path / "settings.json"
    schema.write_text('[// defaults\n{"key":"locale","default":"en",},]')
    monkeypatch.setattr(settings.base, "settings_file_dir", str(schema))
    assert settings.get("locale") == "en"
    assert json.loads(config_path.read_text()) == {"locale": "en"}


@pytest.mark.parametrize("content", ['{"locale":', '["not a settings object"]'])
def test_malformed_settings_are_not_reset(settings, tmp_path, content):
    path = tmp_path / "minify_config.json"
    path.write_text(content)
    with pytest.raises(ValueError, match="file left unchanged"):
        settings.set("theme", "light")
    assert path.read_text() == content


def test_failed_write_keeps_old_file(settings, tmp_path, monkeypatch):
    path = tmp_path / "mods.json"
    path.write_text('{"Old mod": true}')

    def broken_dump(data, file, **kwargs):
        file.write('{"partial"')
        file.flush()
        assert json.loads(path.read_text()) == {"Old mod": True}
        raise OSError("disk full")

    monkeypatch.setattr(settings.jsonc, "dump", broken_dump)
    with pytest.raises(OSError, match="disk full"):
        settings.update_json_file(str(path), "New mod", True)
    assert path.read_text() == '{"Old mod": true}'
    assert list(tmp_path.iterdir()) == [path]


def test_simultaneous_updates_keep_settings_and_mods(settings, tmp_path):
    path = tmp_path / "minify_config.json"
    path.write_text('{/* preserve existing keys */"theme":"dark",}')
    mods = [f"mod-{n}" for n in range(24)]

    def update(n):
        settings.get("locale", "en")
        settings.set(f"key-{n}", n)
        settings.set_mod(mods[n], {"n": n})

    with ThreadPoolExecutor(max_workers=8) as threads:
        list(threads.map(update, range(len(mods))))
    assert json.loads(path.read_text()) == {
        "theme": "dark",
        "locale": "en",
        "modconf": {name: {"n": n} for n, name in enumerate(mods)},
        **{f"key-{n}": n for n in range(len(mods))},
    }
