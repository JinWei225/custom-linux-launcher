"""XDG locations used by the launcher."""

import os
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / fallback


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "launcher"


def config_file() -> Path:
    return config_dir() / "config.toml"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "launcher"
