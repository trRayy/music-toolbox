from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import Any


BASE_DIR = Path(__file__).resolve().parent

SAFE_MYSQL_DEFAULTS: dict[str, Any] = {
    "host": "localhost",
    "port": 3306,
    "user": "music_user",
    "password": "",
    "db": "music_db",
    "charset": "utf8mb4",
    "connect_timeout": 5,
    "read_timeout": 10,
    "write_timeout": 10,
}

MYSQL_ENV_KEYS = {
    "MYSQL_HOST": "host",
    "MYSQL_PORT": "port",
    "MYSQL_USER": "user",
    "MYSQL_PASSWORD": "password",
    "MYSQL_DB": "db",
    "MYSQL_CHARSET": "charset",
    "MYSQL_CONNECT_TIMEOUT": "connect_timeout",
    "MYSQL_READ_TIMEOUT": "read_timeout",
    "MYSQL_WRITE_TIMEOUT": "write_timeout",
}

INTEGER_MYSQL_KEYS = {"port", "connect_timeout", "read_timeout", "write_timeout"}


def _load_local_config() -> ModuleType | None:
    config_path = BASE_DIR / "config.py"
    if not config_path.exists():
        return None

    spec = importlib.util.spec_from_file_location("local_config", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load local config: {config_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOCAL_CONFIG = _load_local_config()


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def get_setting(name: str, default: Any = "") -> Any:
    if name in os.environ:
        return os.environ[name]
    if LOCAL_CONFIG is not None and hasattr(LOCAL_CONFIG, name):
        return getattr(LOCAL_CONFIG, name)
    return default


def get_int_setting(name: str, default: int) -> int:
    return _coerce_int(get_setting(name, default), default)


def get_mysql_config(*, autocommit: bool | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict(SAFE_MYSQL_DEFAULTS)

    if LOCAL_CONFIG is not None and hasattr(LOCAL_CONFIG, "MYSQL_CONFIG"):
        config.update(getattr(LOCAL_CONFIG, "MYSQL_CONFIG"))

    for env_name, key in MYSQL_ENV_KEYS.items():
        if env_name in os.environ:
            value: Any = os.environ[env_name]
            if key in INTEGER_MYSQL_KEYS:
                value = _coerce_int(value, int(config.get(key, SAFE_MYSQL_DEFAULTS[key])))
            config[key] = value

    if overrides:
        config.update({key: value for key, value in overrides.items() if value is not None})

    if autocommit is not None:
        config["autocommit"] = autocommit

    return config
