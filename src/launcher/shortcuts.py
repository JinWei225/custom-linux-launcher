"""Global hotkeys as GNOME custom keybindings, kept in sync with the config.

GNOME on Wayland lets no app grab keys itself; custom keybindings (the ones in
Settings → Keyboard → Custom Shortcuts) run a command instead. The launcher owns the
entries whose dconf path starts with `launcher-` and rewrites exactly those; everything
else is only read, to detect clashes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import accel  # noqa: E402
from .config import Config  # noqa: E402

log = logging.getLogger(__name__)

MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = MEDIA_KEYS + ".custom-keybinding"
CUSTOM_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
OWNED_PREFIX = "launcher-"
# Schemas whose keys are keyboard shortcuts, with a friendly owner name.
SYSTEM_SCHEMAS = {
    "org.gnome.desktop.wm.keybindings": "GNOME (windows)",
    "org.gnome.shell.keybindings": "GNOME Shell",
    "org.gnome.mutter.keybindings": "GNOME (mutter)",
    "org.gnome.mutter.wayland.keybindings": "GNOME (mutter)",
    MEDIA_KEYS: "GNOME (media keys)",
}
EXTENSION_DIRS = (
    Path.home() / ".local/share/gnome-shell/extensions",
    Path("/usr/share/gnome-shell/extensions"),
)


@dataclass(frozen=True)
class Binding:
    key: str  # dconf path component, e.g. "launcher-files"
    name: str  # shown in GNOME Settings
    command: str
    accel: str


def launcher_command() -> str:
    installed = Path.home() / ".local/bin/launcher"
    return str(installed) if installed.exists() else os.path.abspath(sys.argv[0])


def slug(text: str) -> str:
    """Stable, dconf-safe path component; the hash keeps similar names apart."""
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "item"
    return f"{base}-{zlib.crc32(text.encode()):08x}"


def desired_bindings(config: Config, command: str) -> list[Binding]:
    run = shlex.quote(command)
    modes = {
        "launcher": ("launcher-main", "Launcher", run),
        "files": ("launcher-files", "Launcher: Files", f"{run} --mode files"),
        "clipboard": ("launcher-clipboard", "Launcher: Clipboard", f"{run} --mode clipboard"),
        "snippets": ("launcher-snippets", "Launcher: Snippets", f"{run} --mode snippets"),
    }
    bindings = []
    for mode, (key, name, cmd) in modes.items():
        if hotkey := getattr(config.shortcuts, mode):
            bindings.append(Binding(key, name, cmd, hotkey))
    for app_id, app in config.apps.items():
        if app.hotkey:
            bindings.append(
                Binding(
                    f"{OWNED_PREFIX}app-{slug(app_id)}",
                    f"Launcher: {app_id.removesuffix('.desktop')}",
                    f"{run} --run {shlex.quote('app:' + app_id)}",
                    app.hotkey,
                )
            )
    for link in config.quicklinks:
        if link.hotkey:
            bindings.append(
                Binding(
                    f"{OWNED_PREFIX}link-{slug(link.name)}",
                    f"Launcher: {link.name}",
                    f"{run} --run {shlex.quote('quicklink:' + link.name)}",
                    link.hotkey,
                )
            )
    return bindings


# --- reading what is already bound ----------------------------------------------------


def _extension_schema(uuid: str) -> tuple[Gio.SettingsSchema, str] | None:
    """The settings schema of an installed extension, and its display name."""
    for base in EXTENSION_DIRS:
        folder = base / uuid
        if not folder.is_dir():
            continue
        try:
            meta = json.loads((folder / "metadata.json").read_text())
        except (OSError, ValueError):
            return None
        # Some extensions (Ubuntu Dock) hardcode their schema instead of declaring it.
        schema_id = meta.get("settings-schema") or _schema_id_from_code(folder)
        if not schema_id:
            return None
        source = Gio.SettingsSchemaSource.get_default()
        if (folder / "schemas").is_dir():
            try:
                source = Gio.SettingsSchemaSource.new_from_directory(
                    str(folder / "schemas"), source, False
                )
            except GLib.Error:
                pass
        schema = source.lookup(schema_id, True)
        return (schema, meta.get("name", uuid)) if schema is not None else None
    return None


_SCHEMA_ID = re.compile(r"org\.gnome\.shell\.extensions\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*")


def _schema_id_from_code(folder: Path) -> str | None:
    for js in sorted(folder.glob("*.js")):
        try:
            if match := _SCHEMA_ID.search(js.read_text(errors="replace")):
                return match.group(0)
        except OSError:
            continue
    return None


def _schema_accels(schema: Gio.SettingsSchema, settings: Gio.Settings, owner: str):
    for key in schema.list_keys():
        type_string = schema.get_key(key).get_value_type().dup_string()
        if type_string == "as":
            values = settings.get_strv(key)
        elif type_string == "s":
            values = [settings.get_string(key)]
        else:
            continue
        for value in values:
            if value and accel.normalize(value) is not None and value.lower() != "disabled":
                yield value, f"{owner}: {key}"


def system_bindings(include_owned: bool = False) -> dict[str, str]:
    """Every shortcut currently in use: normalized accel (casefolded) -> owner."""
    found: dict[str, str] = {}

    def add(value: str, owner: str) -> None:
        canonical = accel.normalize(value)
        if canonical is not None:
            found.setdefault(canonical.casefold(), owner)

    source = Gio.SettingsSchemaSource.get_default()
    for schema_id, owner in SYSTEM_SCHEMAS.items():
        schema = source.lookup(schema_id, True)
        if schema is not None:
            for value, who in _schema_accels(schema, Gio.Settings.new(schema_id), owner):
                add(value, who)

    shell = source.lookup("org.gnome.shell", True)
    enabled = Gio.Settings.new("org.gnome.shell").get_strv("enabled-extensions") if shell else []
    for uuid in enabled:
        found_schema = _extension_schema(uuid)
        if found_schema is None:
            continue
        schema, name = found_schema
        settings = Gio.Settings.new_full(schema, None, None)
        for value, who in _schema_accels(schema, settings, f"extension {name}"):
            add(value, who)

    for path in Gio.Settings.new(MEDIA_KEYS).get_strv("custom-keybindings"):
        if not include_owned and _is_owned(path):
            continue
        custom = Gio.Settings.new_with_path(CUSTOM_SCHEMA, path)
        binding = custom.get_string("binding")
        if binding:
            add(binding, f"custom shortcut '{custom.get_string('name')}'")
    return found


def _is_owned(path: str) -> bool:
    return path.rstrip("/").rsplit("/", 1)[-1].startswith(OWNED_PREFIX)


def _runs_launcher(command: str, launcher: str) -> bool:
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    return bool(argv) and (argv[0] == launcher or os.path.basename(argv[0]) == "launcher")


# --- syncing ---------------------------------------------------------------------------


def sync(config: Config, command: str | None = None) -> list[str]:
    """Make the launcher-owned GNOME shortcuts match the config. Returns problems."""
    command = command or launcher_command()
    desired = desired_bindings(config, command)
    media = Gio.Settings.new(MEDIA_KEYS)
    paths = list(media.get_strv("custom-keybindings"))
    wanted = {accel.normalize(b.accel).casefold() for b in desired}

    # Custom shortcuts made by hand (or by us before M3) that just run the launcher with
    # a key we are about to own are replaced rather than reported as clashes.
    for path in list(paths):
        if _is_owned(path):
            continue
        custom = Gio.Settings.new_with_path(CUSTOM_SCHEMA, path)
        binding = accel.normalize(custom.get_string("binding") or "")
        if (
            binding
            and binding.casefold() in wanted
            and _runs_launcher(custom.get_string("command"), command)
        ):
            log.info(
                "replacing custom shortcut '%s' with a launcher-managed one",
                custom.get_string("name"),
            )
            _reset(custom)
            paths.remove(path)

    taken = system_bindings(include_owned=False)
    problems = []
    keep = set()
    for binding in desired:
        owner = taken.get(accel.normalize(binding.accel).casefold())
        if owner is not None:
            problems.append(f"{binding.name}: {binding.accel} is already used by {owner}")
            continue
        path = f"{CUSTOM_BASE}{binding.key}/"
        custom = Gio.Settings.new_with_path(CUSTOM_SCHEMA, path)
        for key, value in (
            ("name", binding.name),
            ("command", binding.command),
            ("binding", binding.accel),
        ):
            if custom.get_string(key) != value:
                custom.set_string(key, value)
        keep.add(path)
        if path not in paths:
            paths.append(path)

    for path in list(paths):
        if _is_owned(path) and path not in keep:
            _reset(Gio.Settings.new_with_path(CUSTOM_SCHEMA, path))
            paths.remove(path)

    if paths != list(media.get_strv("custom-keybindings")):
        media.set_strv("custom-keybindings", paths)
    Gio.Settings.sync()
    for problem in problems:
        log.warning("shortcut not set: %s", problem)
    return problems


def _reset(custom: Gio.Settings) -> None:
    for key in ("name", "command", "binding"):
        custom.reset(key)
