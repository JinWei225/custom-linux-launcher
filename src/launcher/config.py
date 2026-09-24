"""Loading and validating ~/.config/launcher/config.toml.

Pure Python (no GTK) so it can be unit tested. A config that fails validation raises
ConfigError; the app then keeps running with the last good config.
"""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class UIConfig:
    width: int = 680
    max_results: int = 8
    hide_on_focus_loss: bool = True


@dataclass(frozen=True)
class Config:
    ui: UIConfig = field(default_factory=UIConfig)


# Inclusive (min, max) bounds for numeric settings, keyed by "section.key".
_RANGES: dict[str, tuple[int, int]] = {
    "ui.width": (400, 1600),
    "ui.max_results": (1, 20),
}

DEFAULT_CONFIG_TEXT = """\
# Launcher configuration. Changes are applied as soon as the file is saved.

[ui]
width = 680               # window width in pixels (400-1600)
max_results = 8           # rows shown at once (1-20)
hide_on_focus_loss = true # hide the launcher when another window gets focus
"""


def parse_config(data: dict[str, Any]) -> tuple[Config, list[str]]:
    """Build a Config from parsed TOML. Returns the config and non-fatal warnings."""
    warnings: list[str] = []
    config = _build(Config, data, "", warnings)
    return config, warnings


def load_config(path: Path) -> tuple[Config, list[str]]:
    """Load the config file, falling back to defaults when it does not exist."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Config(), []
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path.name}: {e}") from e
    return parse_config(data)


def ensure_config(path: Path) -> None:
    """Write the default config file if none exists yet."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")


def _build(cls: type, data: Any, prefix: str, warnings: list[str]) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"[{prefix}] must be a table")
    fields = {f.name: f for f in dataclasses.fields(cls)}
    for key in data:
        if key not in fields:
            warnings.append(f"unknown setting '{prefix}{key}' ignored")

    values: dict[str, Any] = {}
    for name, f in fields.items():
        if name not in data:
            continue
        key = f"{prefix}{name}"
        value = data[name]
        default = f.default_factory() if f.default_factory is not dataclasses.MISSING else f.default
        if dataclasses.is_dataclass(default):
            values[name] = _build(type(default), value, f"{key}.", warnings)
        else:
            values[name] = _check_value(key, value, default)
    return cls(**values)


def _check_value(key: str, value: Any, default: Any) -> Any:
    expected = type(default)
    # bool is a subclass of int, so check it explicitly in both directions.
    if isinstance(value, bool) != (expected is bool) or not isinstance(value, expected):
        raise ConfigError(
            f"'{key}' must be {expected.__name__}, got {type(value).__name__} ({value!r})"
        )
    bounds = _RANGES.get(key)
    if bounds and not bounds[0] <= value <= bounds[1]:
        raise ConfigError(f"'{key}' must be between {bounds[0]} and {bounds[1]}, got {value}")
    return value
