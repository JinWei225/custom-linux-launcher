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

from . import accel


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
    hotkey: str = ""  # global shortcut, e.g. "<Super><Shift>g"


@dataclass(frozen=True)
class AppSettings:
    alias: str = ""
    hotkey: str = ""


@dataclass(frozen=True)
class Snippet:
    """Text pasted from the launcher and/or expanded by espanso when its trigger is typed.

    body may contain {date}, {date:<strftime format>}, {clipboard} and {cursor}."""

    name: str
    body: str
    trigger: str = ""  # typed expansion, e.g. ";sig" (espanso)
    alias: str = ""  # exact-match search keyword in the launcher
    hotkey: str = ""  # global shortcut that pastes the snippet


@dataclass(frozen=True)
class ShortcutsConfig:
    """Global shortcuts for the launcher's modes ("" = not bound)."""

    launcher: str = "<Super><Shift>Return"
    files: str = "<Super><Shift>f"
    clipboard: str = "<Super><Shift>v"
    clipboard_pause: str = "<Super><Shift>p"  # pause / resume clipboard recording
    snippets: str = "<Super><Shift>s"


# Password managers: never record what they copy (they also mark secrets with a hint).
DEFAULT_CLIPBOARD_EXCLUDES = (
    "org.keepassxc.KeePassXC",
    "KeePassXC",
    "Bitwarden",
    "com.bitwarden.desktop",
    "1Password",
    "org.gnome.World.Secrets",
)
# Terminals paste with Ctrl+Shift+V instead of Ctrl+V.
DEFAULT_TERMINALS = (
    "org.gnome.Ptyxis",
    "com.mitchellh.ghostty",
    "org.gnome.Console",
    "org.gnome.Terminal",
    "gnome-terminal-server",
    "kitty",
    "Alacritty",
    "org.wezfurlong.wezterm",
    "foot",
    "com.gexperts.Tilix",
    "org.kde.konsole",
    "xterm",
)


@dataclass(frozen=True)
class ClipboardConfig:
    enabled: bool = True
    max_entries: int = 500  # pinned entries are never removed and don't count
    max_days: int = 30
    max_image_mb: int = 20
    exclude_apps: tuple[str, ...] = field(
        default=DEFAULT_CLIPBOARD_EXCLUDES, metadata={"list": str}
    )
    terminal_apps: tuple[str, ...] = field(default=DEFAULT_TERMINALS, metadata={"list": str})


DEFAULT_EXCLUDES = (
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "*.part",
    "*.crdownload",
    "*.tmp",
    ".~lock.*",
)


@dataclass(frozen=True)
class FilesConfig:
    folders: tuple[str, ...] = field(default=("~/Downloads", "~/Documents"), metadata={"list": str})
    exclude: tuple[str, ...] = field(default=DEFAULT_EXCLUDES, metadata={"list": str})
    max_depth: int = 8  # levels below each folder
    show_hidden: bool = False  # names starting with "."


@dataclass(frozen=True)
class Config:
    ui: UIConfig = field(default_factory=UIConfig)
    files: FilesConfig = field(default_factory=FilesConfig)
    shortcuts: ShortcutsConfig = field(default_factory=ShortcutsConfig)
    clipboard: ClipboardConfig = field(default_factory=ClipboardConfig)
    # desktop file id ("code.desktop") -> alias / hotkey
    apps: dict[str, AppSettings] = field(default_factory=dict, metadata={"tables": AppSettings})
    quicklinks: tuple[QuickLink, ...] = field(
        default_factory=tuple, metadata={"items": QuickLink, "key": "quicklink"}
    )
    # Normally kept in snippets.toml next to config.toml (see load_config).
    snippets: tuple[Snippet, ...] = field(
        default_factory=tuple, metadata={"items": Snippet, "key": "snippet"}
    )


# Inclusive (min, max) bounds for numeric settings, keyed by "section.key".
_RANGES: dict[str, tuple[int, int]] = {
    "ui.width": (400, 1600),
    "ui.max_results": (1, 20),
    "files.max_depth": (1, 32),
    "clipboard.max_entries": (10, 10_000),
    "clipboard.max_days": (1, 3650),
    "clipboard.max_image_mb": (1, 200),
}

DEFAULT_CONFIG_TEXT = """\
# Launcher configuration. Changes are applied as soon as the file is saved.

[ui]
width = 680               # window width in pixels (400-1600)
max_results = 8           # rows shown at once (1-20)
hide_on_focus_loss = true # hide the launcher when another window gets focus
favicons = true           # website icons for quicklinks (fetched via Google's favicon service)

# File search (Super+Shift+F, or `launcher --mode files`).
[files]
folders = ["~/Downloads", "~/Documents"]
# Names (or glob patterns) skipped anywhere below those folders:
exclude = [
  ".git", "node_modules", "__pycache__", ".venv", "venv",
  "*.part", "*.crdownload", "*.tmp", ".~lock.*",
]
max_depth = 8             # how many folder levels deep to search (1-32)
show_hidden = false       # include names starting with "."

# Global shortcuts ("" = none). Easiest to change in Launcher Settings, which checks
# for clashes with GNOME's own shortcuts.
[shortcuts]
launcher = "<Super><Shift>Return"
files = "<Super><Shift>f"
clipboard = "<Super><Shift>v"
clipboard_pause = "<Super><Shift>p"
snippets = "<Super><Shift>s"

# Clipboard history (Super+Shift+V). Needs the Launcher Helper GNOME extension.
[clipboard]
enabled = true
max_entries = 500         # pinned entries are kept forever and don't count
max_days = 30
max_image_mb = 20
# Never recorded (password managers); app ids or window classes:
exclude_apps = [
  "org.keepassxc.KeePassXC", "KeePassXC", "Bitwarden", "com.bitwarden.desktop",
  "1Password", "org.gnome.World.Secrets",
]
# Paste with Ctrl+Shift+V into these:
terminal_apps = [
  "org.gnome.Ptyxis", "com.mitchellh.ghostty", "org.gnome.Console", "org.gnome.Terminal",
  "gnome-terminal-server", "kitty", "Alacritty", "org.wezfurlong.wezterm", "foot",
  "com.gexperts.Tilix", "org.kde.konsole", "xterm",
]

# Per-app alias and hotkey, keyed by desktop file id. In the launcher, select an app
# and press Ctrl+E to set these without editing this file.
# [apps."code.desktop"]
# alias = "code"
# hotkey = "<Super><Shift>c"

# Quicklinks open a URL. Put {query} in the URL to make it a search:
# "g cats" opens the Google URL with {query} replaced by "cats".
# fallback = true offers the search for anything you type.
# hotkey = "<Super><Shift>g" opens the link (or starts a search) from anywhere.
[[quicklink]]
name = "Google Search"
alias = "g"
url = "https://www.google.com/search?q={query}"
fallback = true

# [[quicklink]]
# name = "GitHub"
# alias = "gh"
# url = "https://github.com/"

# Snippets live in snippets.toml in this folder (edit them in Launcher Settings).
"""

SNIPPETS_HEADER = '''\
# Launcher snippets. Edit them in Launcher Settings -> Snippets, or here by hand.
# Snippets with a trigger are expanded as you type by espanso (the launcher writes
# ~/.config/espanso/match/launcher.yml from this file). Placeholders in the body:
#   {date}  {date:%d %B %Y}  {clipboard}  {cursor}
#
# [[snippet]]
# name = "Email signature"
# trigger = ";sig"
# alias = "sig"
# body = """
# Best regards,
# Jinwei"""

'''


def snippets_file(config_path: Path) -> Path:
    return config_path.with_name("snippets.toml")


def parse_config(
    data: dict[str, Any], snippets: dict[str, Any] | None = None
) -> tuple[Config, list[str]]:
    """Build a Config from parsed TOML (config.toml, plus snippets.toml if given).
    Returns the config and non-fatal warnings."""
    warnings: list[str] = []
    data = dict(data)
    if snippets is not None:
        extra = snippets.get("snippet", [])
        for key in snippets:
            if key != "snippet":
                warnings.append(f"snippets.toml: unknown setting '{key}' ignored")
        if not isinstance(extra, list):
            raise ConfigError("snippets.toml: snippets must be [[snippet]] tables")
        own = data.get("snippet", [])
        data["snippet"] = (own if isinstance(own, list) else [own]) + extra
    legacy = data.pop("aliases", None)
    if legacy:
        raise ConfigError(
            "[aliases] was replaced by per-app tables: "
            '[apps."code.desktop"] alias = "code" (or use Ctrl+E in the launcher)'
        )
    config = _build(Config, data, "", warnings)
    _check_links_and_aliases(config)
    _check_snippets(config)
    _check_hotkeys(config)
    return config, warnings


def load_config(path: Path) -> tuple[Config, list[str]]:
    """Load config.toml and snippets.toml; a missing file counts as empty."""
    return parse_texts(_read(path), _read(snippets_file(path)))


def parse_texts(config_text: str | None, snippets_text: str | None) -> tuple[Config, list[str]]:
    data = _loads(config_text, "config.toml") if config_text is not None else {}
    snippets = _loads(snippets_text, "snippets.toml") if snippets_text is not None else None
    return parse_config(data, snippets)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e


def _loads(text: str, name: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{name}: {e}") from e


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
        elif "list" in f.metadata:
            if not isinstance(value, list):
                raise ConfigError(f"'{path}' must be a list")
            values[f.name] = tuple(
                _check_value(f"{path}[{i + 1}]", v, f.metadata["list"]) for i, v in enumerate(value)
            )
        elif "tables" in f.metadata:
            if not isinstance(value, dict):
                raise ConfigError(f"[{path}] must be a table")
            values[f.name] = {
                k: _build(f.metadata["tables"], v, f'{path}."{k}".', warnings)
                for k, v in value.items()
            }
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


def _check_links_and_aliases(config: Config) -> None:
    seen: dict[str, str] = {}

    def claim(alias: str, owner: str) -> None:
        if not alias:
            return
        if any(ch.isspace() for ch in alias):
            raise ConfigError(f"{owner}: alias {alias!r} must not contain spaces")
        key = alias.casefold()
        if key in seen:
            raise ConfigError(f"alias '{alias}' is used by both {seen[key]} and {owner}")
        seen[key] = owner

    for app_id, app in config.apps.items():
        claim(app.alias, f"app '{app_id}'")
    for snippet in config.snippets:
        claim(snippet.alias, f"snippet '{snippet.name}'")
    for i, link in enumerate(config.quicklinks, 1):
        owner = f"quicklink '{link.name}'"
        if "://" not in link.url and not link.url.startswith("mailto:"):
            raise ConfigError(f"quicklink[{i}] ({link.name}): url {link.url!r} needs a scheme")
        if link.fallback and "{query}" not in link.url:
            raise ConfigError(f"{owner}: fallback = true needs {{query}} in the url")
        claim(link.alias, owner)
    names = [link.name.casefold() for link in config.quicklinks]
    if len(set(names)) != len(names):
        raise ConfigError("two quicklinks have the same name; names must be unique")


def _check_snippets(config: Config) -> None:
    names: set[str] = set()
    triggers: dict[str, str] = {}
    for snippet in config.snippets:
        owner = f"snippet '{snippet.name}'"
        if not snippet.name.strip():
            raise ConfigError("every snippet needs a name")
        if snippet.name.casefold() in names:
            raise ConfigError(f"two snippets are named '{snippet.name}'; names must be unique")
        names.add(snippet.name.casefold())
        if not snippet.body:
            raise ConfigError(f"{owner} has an empty body")
        if snippet.trigger:
            if any(ch.isspace() for ch in snippet.trigger):
                raise ConfigError(f"{owner}: trigger {snippet.trigger!r} must not contain spaces")
            if len(snippet.trigger) < 2:
                raise ConfigError(f"{owner}: trigger {snippet.trigger!r} is too short")
            if snippet.trigger in triggers:
                raise ConfigError(
                    f"trigger '{snippet.trigger}' is used by both {triggers[snippet.trigger]} "
                    f"and {owner}"
                )
            triggers[snippet.trigger] = owner


def hotkey_owners(config: Config) -> list[tuple[str, str]]:
    """(hotkey, owner description) for every hotkey set in the config."""
    owners = [
        (getattr(config.shortcuts, f.name), f"the launcher's {f.name} shortcut")
        for f in dataclasses.fields(ShortcutsConfig)
    ]
    owners += [(app.hotkey, f"app '{app_id}'") for app_id, app in config.apps.items()]
    owners += [(link.hotkey, f"quicklink '{link.name}'") for link in config.quicklinks]
    owners += [(s.hotkey, f"snippet '{s.name}'") for s in config.snippets]
    return [(key, owner) for key, owner in owners if key]


def _check_hotkeys(config: Config) -> None:
    seen: dict[str, str] = {}
    for key, owner in hotkey_owners(config):
        if problem := accel.hotkey_problem(key):
            raise ConfigError(f"{owner}: {problem}")
        canonical = accel.normalize(key).casefold()
        if canonical in seen:
            raise ConfigError(f"{key} is used by both {seen[canonical]} and {owner}")
        seen[canonical] = owner
