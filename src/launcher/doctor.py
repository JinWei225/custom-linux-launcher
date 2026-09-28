"""Setup check: is everything the launcher relies on in place?

`gather()` reads the system (services, GNOME Shell, gsettings, files); `evaluate()`
turns those facts into checks, each ok / warning / error with a fix. `evaluate` is
pure, so every rule is unit tested. Used by `launcher --doctor`, `make install`, the
Status page in Launcher Settings and the launcher's banner. No GTK here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_ID, paths
from .config import Config, ConfigError, load_config

OK, WARNING, ERROR = "ok", "warning", "error"
EXTENSION_UUID = "launcher-helper@jinwei.github.io"  # as in helper.py
HELPER_VERSION = 2  # the extension version this launcher needs (snippet cursor: v2)
# GNOME Shell's ExtensionState values
EXT_ACTIVE, EXT_INACTIVE, EXT_ERROR, EXT_OUT_OF_DATE = 1, 2, 3, 4
DAY = 86400


@dataclass(frozen=True)
class Check:
    key: str
    title: str
    status: str  # ok, warning, error
    detail: str
    fix: str = ""  # what to do about it, for people
    command: tuple[str, ...] = ()  # runs the fix (Settings' Fix button); never needs sudo
    shell: str = ""  # a command to copy and run yourself (Settings' Copy button)


@dataclass
class Facts:
    config: Config = field(default_factory=Config)
    config_error: str | None = None
    config_warnings: list[str] = field(default_factory=list)
    launcher: str = "launcher"  # the command that runs this launcher
    service: str = ""  # `systemctl --user is-active`: active, inactive, failed…; "" unknown
    service_enabled: bool = False
    daemon_running: bool = False  # the launcher's bus name has an owner
    shell_version: str = ""  # "50.1"; "" if GNOME Shell couldn't be asked
    extensions_enabled: bool = True  # GNOME's "Use Extensions" switch
    extension_on_disk: int | None = None  # version in the installed metadata.json
    extension_shells: tuple[str, ...] = ()  # the installed metadata's shell-version
    extension_state: int | None = None  # as GNOME Shell reports it; None if unknown to it
    extension_enabled: bool = False
    extension_loaded: int | None = None  # the version GNOME Shell has loaded
    extension_error: str = ""
    espanso: bool = False  # the espanso command is installed
    espanso_service: str = ""
    espanso_file: bool = False  # espanso's launcher.yml exists
    gi_cairo: bool = True
    shortcuts_missing: list[str] = field(default_factory=list)
    shortcut_clashes: list[str] = field(default_factory=list)
    notes_writable: bool = True
    rates_age: float | None = None  # seconds since the rates were downloaded
    missing_folders: list[str] = field(default_factory=list)


# --- rules -----------------------------------------------------------------------------


def evaluate(f: Facts) -> list[Check]:
    return [
        _config(f),
        _service(f),
        _extension(f),
        _shortcuts(f),
        _espanso(f),
        _notes(f),
        _pdf(f),
        _rates(f),
        _folders(f),
    ]


def _config(f: Facts) -> Check:
    title = "Config file"
    if f.config_error:
        return Check(
            "config",
            title,
            ERROR,
            f.config_error,
            "Fix the file (Settings → General → Open in Text Editor); the launcher "
            "keeps using the last good settings until then.",
        )
    if f.config_warnings:
        return Check(
            "config",
            title,
            WARNING,
            "; ".join(f.config_warnings),
            "Remove or correct those settings.",
        )
    return Check("config", title, OK, "No problems")


def _service(f: Facts) -> Check:
    title = "Launcher service"
    start = ("systemctl", "--user", "enable", "--now", "launcher.service")
    if f.service == "active":
        if not f.service_enabled:
            return Check(
                "service",
                title,
                WARNING,
                "Running, but won't start after you log in",
                "Enable it.",
                start,
            )
        return Check("service", title, OK, "Running")
    if f.daemon_running:
        return Check(
            "service",
            title,
            WARNING,
            "The launcher is running, but not as the service (make dev?)",
            "Stop it and start the service, so it survives a crash or log out.",
            start,
        )
    if not f.service:
        return Check(
            "service",
            title,
            ERROR,
            "Not installed as a service",
            "Run make install in the launcher's folder.",
        )
    state = "crashed and stopped" if f.service == "failed" else f.service
    return Check(
        "service",
        title,
        ERROR,
        f"Not running ({state}): the shortcuts do nothing",
        "Start it; if it stops again, see its log: journalctl --user -u launcher",
        start,
    )


def _extension(f: Facts) -> Check:
    title = "Helper extension"
    broken = "clipboard history and paste don't work"
    install = "Run make install-extension in the launcher's folder, then log out and back in."
    if f.extension_on_disk is None and f.extension_state is None:
        return Check("extension", title, ERROR, f"Not installed: {broken}", install)
    major = f.shell_version.split(".")[0]
    if major and f.extension_shells and major not in f.extension_shells:
        return Check(
            "extension",
            title,
            ERROR,
            f"Doesn't declare support for GNOME {major}: {broken}",
            f'Check it still works (make test-extension), then add "{major}" to '
            "shell-version in its metadata.json and reinstall it.",
        )
    if not f.shell_version:
        return Check("extension", title, WARNING, "Couldn't ask GNOME Shell about it")
    if not f.extensions_enabled:
        return Check(
            "extension",
            title,
            ERROR,
            f"GNOME's extensions are turned off: {broken}",
            "Turn on Extensions in the Extensions app.",
            ("gsettings", "set", "org.gnome.shell", "disable-user-extensions", "false"),
        )
    if f.extension_state is None:
        return Check(
            "extension",
            title,
            ERROR,
            f"Installed, but GNOME Shell hasn't loaded it yet: {broken}",
            "Log out and back in.",
        )
    if f.extension_state == EXT_ERROR:
        return Check(
            "extension",
            title,
            ERROR,
            f"Failed to start: {f.extension_error}",
            "Reinstall it (make install-extension), then log out and back in.",
        )
    if f.extension_state == EXT_OUT_OF_DATE:
        return Check(
            "extension", title, ERROR, f"GNOME Shell says it is out of date: {broken}", install
        )
    if f.extension_state != EXT_ACTIVE:
        return Check(
            "extension",
            title,
            ERROR,
            f"Turned off: {broken}",
            "Turn it on.",
            ("gnome-extensions", "enable", EXTENSION_UUID),
        )
    loaded, on_disk = f.extension_loaded or 0, f.extension_on_disk or 0
    if on_disk > loaded:
        return Check(
            "extension",
            title,
            WARNING,
            f"Version {on_disk} is installed but {loaded} is running",
            "Log out and back in to load the new version.",
        )
    if loaded < HELPER_VERSION:
        return Check(
            "extension",
            title,
            WARNING,
            f"Version {loaded} is older than this launcher needs ({HELPER_VERSION}): "
            "snippets can't place the cursor",
            install,
        )
    return Check("extension", title, OK, f"Version {loaded} is active")


def _shortcuts(f: Facts) -> Check:
    title = "Keyboard shortcuts"
    if f.shortcut_clashes:
        return Check(
            "shortcuts",
            title,
            WARNING,
            "; ".join(f.shortcut_clashes),
            "Pick other keys in Settings → Shortcuts, or free them in GNOME Settings → Keyboard.",
        )
    if f.shortcuts_missing:
        return Check(
            "shortcuts",
            title,
            WARNING,
            _listing("Not registered with GNOME", f.shortcuts_missing),
            "Reload the launcher, which registers them.",
            (f.launcher, "--reload"),
        )
    return Check("shortcuts", title, OK, "All registered")


def _espanso(f: Facts) -> Check:
    title = "Snippet triggers (espanso)"
    triggers = [s for s in f.config.snippets if s.trigger]
    if not triggers:
        return Check("espanso", title, OK, "No snippet has a trigger, so espanso isn't needed")
    if not f.espanso:
        return Check(
            "espanso",
            title,
            WARNING,
            f"espanso isn't installed: {len(triggers)} trigger(s) won't expand",
            "Install espanso for Wayland (espanso.org/install).",
        )
    if f.espanso_service != "active":
        return Check(
            "espanso",
            title,
            WARNING,
            "espanso isn't running: triggers won't expand",
            "Start it.",
            ("systemctl", "--user", "restart", "espanso.service"),
        )
    if not f.espanso_file:
        return Check(
            "espanso",
            title,
            WARNING,
            "espanso hasn't been given the snippets",
            "Reload the launcher, which writes them.",
            (f.launcher, "--reload"),
        )
    return Check("espanso", title, OK, f"Running with {len(triggers)} trigger(s)")


def _notes(f: Facts) -> Check:
    title = "Notes folder"
    folder = f.config.notes.folder
    if not f.notes_writable:
        return Check(
            "notes",
            title,
            ERROR,
            f"{folder} can't be written: notes can't be saved",
            "Choose another folder in Settings → General, or fix its permissions.",
        )
    return Check("notes", title, OK, folder)


def _pdf(f: Facts) -> Check:
    title = "Notes PDF export"
    if not f.gi_cairo:
        return Check(
            "pdf",
            title,
            WARNING,
            "The python3-gi-cairo package is missing",
            "Install it: sudo apt install python3-gi-cairo",
            shell="sudo apt install python3-gi-cairo",
        )
    return Check("pdf", title, OK, "Ready")


def _rates(f: Facts) -> Check:
    title = "Exchange rates"
    if not f.config.converters.currency:
        return Check("rates", title, OK, "Currency conversion is turned off")
    fix = "Check the internet connection, then Settings → Converters → Refresh Now."
    if f.rates_age is None:
        return Check("rates", title, WARNING, "Not downloaded yet: currencies can't convert", fix)
    stale = max(2 * f.config.converters.refresh_hours * 3600, 2 * DAY)
    age = _age(f.rates_age)
    if f.rates_age > stale:
        return Check("rates", title, WARNING, f"Last downloaded {age} ago", fix)
    return Check("rates", title, OK, f"Downloaded {age} ago")


def _folders(f: Facts) -> Check:
    title = "File search folders"
    if f.missing_folders:
        return Check(
            "folders",
            title,
            WARNING,
            "Missing: " + ", ".join(f.missing_folders),
            "Remove them in Settings → Files, or create them.",
        )
    return Check("folders", title, OK, ", ".join(f.config.files.folders) or "None set")


def _listing(what: str, items: list[str], shown: int = 3) -> str:
    """ "What: a, b, c and 2 more" """
    more = f" and {len(items) - shown} more" if len(items) > shown else ""
    return f"{what}: {', '.join(items[:shown])}{more}"


def _age(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(int(seconds // 60), 1)} min"
    if seconds < 2 * DAY:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // DAY)} days"


# --- in the launcher's banner ----------------------------------------------------------

# The config banner already shows config and shortcut problems; the service is the
# launcher itself, which is evidently running.
NOT_IN_BANNER = {"config", "shortcuts", "service"}


def fingerprint(check: Check) -> str:
    """Dismissing a problem hides it until it changes."""
    return f"{check.key}:{check.detail}"


def dismissed_file() -> Path:
    return paths.state_dir() / "dismissed.json"


def load_dismissed() -> set[str]:
    try:
        data = json.loads(dismissed_file().read_text(encoding="utf-8"))
        return {str(x) for x in data} if isinstance(data, list) else set()
    except (OSError, ValueError):
        return set()


def save_dismissed(dismissed: set[str]) -> None:
    try:
        dismissed_file().parent.mkdir(parents=True, exist_ok=True)
        dismissed_file().write_text(json.dumps(sorted(dismissed)), encoding="utf-8")
    except OSError:
        pass


def launcher_problems(checks: list[Check], dismissed: set[str]) -> list[Check]:
    """What the launcher's banner shows, most serious first."""
    shown = [
        c for c in checks
        if c.status != OK and c.key not in NOT_IN_BANNER and fingerprint(c) not in dismissed
    ]  # fmt: skip
    return sorted(shown, key=lambda c: c.status != ERROR)


def still_dismissed(checks: list[Check], dismissed: set[str]) -> set[str]:
    """Forget dismissals of problems that went away, so they show if they come back."""
    return dismissed & {fingerprint(c) for c in checks if c.status != OK}


def banner_text(problems: list[Check]) -> str:
    if not problems:
        return ""
    first = problems[0]
    more = len(problems) - 1
    text = f"{first.title}: {first.detail}"
    return text + (f" (and {more} more)" if more else "")


# --- reading the system ----------------------------------------------------------------


def gather(config_path: Path | None = None) -> Facts:
    f = Facts()
    try:
        f.config, f.config_warnings = load_config(config_path or paths.config_file())
    except ConfigError as e:
        f.config_error = str(e)
    from . import shortcuts

    f.launcher = shortcuts.launcher_command()
    f.service, f.service_enabled = _unit("launcher.service")
    f.espanso = shutil.which("espanso") is not None
    f.espanso_service, _ = _unit("espanso.service")
    f.espanso_file = (paths.espanso_match_dir() / "launcher.yml").exists()
    _gather_extension(f)
    try:
        import gi

        gi.require_foreign("cairo")
    except ImportError:
        f.gi_cairo = False
    try:
        f.shortcuts_missing, f.shortcut_clashes = shortcuts.check(f.config)
    except Exception as e:  # no GNOME settings schemas: report, don't crash
        f.shortcut_clashes = [f"couldn't read GNOME's shortcuts ({e})"]
    f.notes_writable = _writable(Path(os.path.expanduser(f.config.notes.folder)))
    f.rates_age = _rates_age()
    f.missing_folders = [
        folder for folder in f.config.files.folders
        if not Path(os.path.expanduser(folder)).is_dir()
    ]  # fmt: skip
    return f


def _unit(name: str) -> tuple[str, bool]:
    """A systemd user unit's state ("active", "failed"…; "" if it isn't installed) and
    whether it starts at login."""
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", name, "-p", "LoadState,ActiveState,UnitFileState"],
            capture_output=True, text=True, timeout=5,
        ).stdout  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return "", False
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if props.get("LoadState") != "loaded":
        return "", False
    return props.get("ActiveState", ""), props.get("UnitFileState") == "enabled"


def _gather_extension(f: Facts) -> None:
    for base in (paths.data_dir().parent / "gnome-shell/extensions",
                 Path("/usr/share/gnome-shell/extensions")):  # fmt: skip
        metadata = base / EXTENSION_UUID / "metadata.json"
        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        f.extension_on_disk = int(data.get("version", 0))
        f.extension_shells = tuple(str(v) for v in data.get("shell-version", []))
        break
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        f.daemon_running = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", (APP_ID,)), None, Gio.DBusCallFlags.NONE,
            2000, None,
        ).unpack()[0]  # fmt: skip
        shell = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None, "org.gnome.Shell", "/org/gnome/Shell",
            "org.gnome.Shell.Extensions", None,
        )  # fmt: skip
        version = shell.get_cached_property("ShellVersion")
        enabled = shell.get_cached_property("UserExtensionsEnabled")
        if version is None:
            return  # GNOME Shell isn't running (e.g. an SSH session)
        f.shell_version = version.unpack()
        f.extensions_enabled = enabled.unpack() if enabled is not None else True
        info = shell.call_sync(
            "GetExtensionInfo", GLib.Variant("(s)", (EXTENSION_UUID,)),
            Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]  # fmt: skip
    except Exception:
        return
    if info:
        f.extension_state = int(info.get("state", 0))
        f.extension_enabled = bool(info.get("enabled", False))
        f.extension_loaded = int(info.get("version", 0))
        f.extension_error = str(info.get("error", ""))


def _writable(folder: Path) -> bool:
    target = folder
    while not target.exists():  # it is created on first use: check where it would go
        if target.parent == target:
            return False
        target = target.parent
    return target.is_dir() and os.access(target, os.W_OK | os.X_OK)


def _rates_age() -> float | None:
    try:
        fetched = json.loads(paths.rates_file().read_text(encoding="utf-8"))["fetched"]
        return max(time.time() - float(fetched), 0.0)
    except (OSError, ValueError, KeyError, TypeError):
        return None


# --- the report ------------------------------------------------------------------------

_MARKS = {OK: "✓", WARNING: "!", ERROR: "✗"}
_COLOURS = {OK: "32", WARNING: "33", ERROR: "31"}


def report(checks: list[Check], color: bool = False) -> str:
    width = max(len(c.title) for c in checks)
    lines = []
    for c in checks:
        mark = _MARKS[c.status]
        if color:
            mark = f"\x1b[{_COLOURS[c.status]}m{mark}\x1b[0m"
        lines.append(f"{mark} {c.title.ljust(width)}  {c.detail}")
        if c.status != OK and c.fix:
            lines.append(f"  {' ' * width}  → {c.fix}")
    problems = [c for c in checks if c.status != OK]
    if problems:
        errors = sum(c.status == ERROR for c in problems)
        lines.append(f"\n{len(problems)} problem(s), {errors} serious.")
    else:
        lines.append("\nEverything is set up.")
    return "\n".join(lines)
