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
    favicons: bool = True


@dataclass(frozen=True)
class QuickLink:
    name: str
    url: str  # may contain {query}; then it works as a search
    alias: str = ""
    icon: str = ""  # themed icon name or absolute path
    fallback: bool = False  # offer "Search <name> for …" for any typed text


@dataclass(frozen=True)
class Config:
    ui: UIConfig = field(default_factory=UIConfig)
    # alias -> desktop file id ("code.desktop") or app name ("Visual Studio Code")
    aliases: dict[str, str] = field(default_factory=dict, metadata={"map": str})
    quicklinks: tuple[QuickLink, ...] = field(
        default_factory=tuple, metadata={"items": QuickLink, "key": "quicklink"}
    )


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
favicons = true           # website icons for quicklinks (fetched via Google's favicon service)

# Short names for apps: alias = "desktop file id" or "App Name".
[aliases]
# code = "code.desktop"
# ff = "Firefox"

# Quicklinks open a URL. Put {query} in the URL to make it a search:
# "g cats" opens the Google URL with {query} replaced by "cats".
# fallback = true offers the search for anything you type.
[[quicklink]]
name = "Google Search"
alias = "g"
url = "https://www.google.com/search?q={query}"
fallback = true

# [[quicklink]]
# name = "GitHub"
# alias = "gh"
# url = "https://github.com/"
"""


def parse_config(data: dict[str, Any]) -> tuple[Config, list[str]]:
    """Build a Config from parsed TOML. Returns the config and non-fatal warnings."""
    warnings: list[str] = []
    config = _build(Config, data, "", warnings)
    _check_links_and_aliases(config, warnings)
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
    where = prefix.rstrip(".") or "top level"
    if not isinstance(data, dict):
        raise ConfigError(f"[{where}] must be a table")
    fields = {f.metadata.get("key", f.name): f for f in dataclasses.fields(cls)}
    for key in data:
        if key not in fields:
            warnings.append(f"unknown setting '{prefix}{key}' ignored")

    values: dict[str, Any] = {}
    for key, f in fields.items():
        path = f"{prefix}{key}"
        if key not in data:
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                raise ConfigError(f"[{where}] is missing required setting '{key}'")
            continue
        value = data[key]
        if "items" in f.metadata:
            if not isinstance(value, list):
                raise ConfigError(f"'{path}' must be a list of tables ([[{key}]])")
            values[f.name] = tuple(
                _build(f.metadata["items"], item, f"{path}[{i + 1}].", warnings)
                for i, item in enumerate(value)
            )
        elif "map" in f.metadata:
            if not isinstance(value, dict):
                raise ConfigError(f"[{path}] must be a table")
            values[f.name] = {
                k: _check_value(f"{path}.{k}", v, f.metadata["map"]) for k, v in value.items()
            }
        else:
            default = f.default if f.default is not dataclasses.MISSING else None
            if f.default_factory is not dataclasses.MISSING:
                default = f.default_factory()
            if dataclasses.is_dataclass(default):
                values[f.name] = _build(type(default), value, f"{path}.", warnings)
            else:
                expected = type(default) if default is not None else _required_type(f)
                values[f.name] = _check_value(path, value, expected)
    return cls(**values)


def _required_type(f: dataclasses.Field) -> type:
    # Required fields have no default to infer the type from; annotations are strings
    # because of `from __future__ import annotations`.
    return {"str": str, "int": int, "bool": bool}[str(f.type)]


def _check_value(key: str, value: Any, expected: type) -> Any:
    # bool is a subclass of int, so check it explicitly in both directions.
    if isinstance(value, bool) != (expected is bool) or not isinstance(value, expected):
        raise ConfigError(
            f"'{key}' must be {expected.__name__}, got {type(value).__name__} ({value!r})"
        )
    bounds = _RANGES.get(key)
    if bounds and not bounds[0] <= value <= bounds[1]:
        raise ConfigError(f"'{key}' must be between {bounds[0]} and {bounds[1]}, got {value}")
    return value


def _check_links_and_aliases(config: Config, warnings: list[str]) -> None:
    seen: dict[str, str] = {}

    def claim(alias: str, owner: str) -> None:
        if not alias:
            return
        if any(ch.isspace() for ch in alias):
            raise ConfigError(f"{owner}: alias {alias!r} must not contain spaces")
        key = alias.casefold()
        if key in seen:
            warnings.append(f"alias '{alias}' is used by both {seen[key]} and {owner}")
        else:
            seen[key] = owner

    for alias, target in config.aliases.items():
        claim(alias, f"app alias '{alias}' ({target})")
    for i, link in enumerate(config.quicklinks, 1):
        owner = f"quicklink '{link.name}'"
        if "://" not in link.url and not link.url.startswith("mailto:"):
            raise ConfigError(f"quicklink[{i}] ({link.name}): url {link.url!r} needs a scheme")
        if link.fallback and "{query}" not in link.url:
            raise ConfigError(f"{owner}: fallback = true needs {{query}} in the url")
        claim(link.alias, owner)
