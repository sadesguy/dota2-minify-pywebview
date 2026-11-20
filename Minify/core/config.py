"""
JSON(C) config files

Interactions with main config and mod configs
"""

import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any, Optional

import jsonc

from core import base, utils

_lock = RLock()  # pywebview dispatches simultaneous requests on separate threads.


def read_json_file(path: str) -> Any:
    settings = path in (base.main_config_file_dir, base.mods_config_dir)
    try:
        with _lock, utils.open_utf8R(path) as file:
            text = file.read()
            data = jsonc.loads(text) if text.strip() else {}
    except FileNotFoundError:
        return {}
    except jsonc.JSONDecodeError as error:
        if settings:
            raise ValueError(f"Invalid config in {path}; file left unchanged: {error}") from error
        return {}
    if settings and not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}; file left unchanged")
    return data


def write_json_file(path: str, data: Any) -> None:
    target = Path(path).resolve()
    with _lock:
        fd, name = tempfile.mkstemp(dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                jsonc.dump(data, file, indent=2)
            if target.is_file():
                os.chmod(name, target.stat().st_mode & 0o777)
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)


def update_json_file(path: str, key: str, value: Any) -> Any:
    with _lock:
        data = read_json_file(path)
        data[key] = value

        if path in (base.main_config_file_dir, base.mods_config_dir):
            data = dict(sorted(data.items()))

        write_json_file(path, data)

    return value


def _get_default_setting(key: str) -> Any:
    schema = read_json_file(base.settings_file_dir)
    if isinstance(schema, list):
        for item in schema:
            if isinstance(item, dict) and item.get("key") == key:
                return item.get("default")
    return None


def get(key: str, default_value: Any = None) -> Any:
    with _lock:
        data = read_json_file(base.main_config_file_dir)

        if key in data:
            return data[key]

        if default_value is None:
            default_value = _get_default_setting(key)

        if default_value is not None:
            return update_json_file(base.main_config_file_dir, key, default_value)

    return None


def set(key: str, value: Any) -> Any:
    return update_json_file(base.main_config_file_dir, key, value)


def get_mod(mod_name: str, default: Optional[dict] = None) -> dict:
    if default is None:
        default = {}
    return get("modconf", {}).get(mod_name, default)


def set_mod(mod_name: str, config_data: dict) -> None:
    with _lock:
        modconf = get("modconf", {})
        modconf[mod_name] = config_data
        set("modconf", modconf)


def get_locale():
    "Returns the valid locale instead of unresolved."
    from core import constants

    return constants.resolve_locale(get("output_locale", "english"))
