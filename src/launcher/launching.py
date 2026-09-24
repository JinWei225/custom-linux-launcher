"""Starting apps and opening URIs so they outlive the launcher.

The launcher runs as launcher.service, and by default every process it spawns lands in
that service's cgroup: `systemctl --user restart launcher` would then kill every app
opened through it. Like GNOME Shell, each launched process is moved into its own
transient scope (app-launcher-<app id>-<pid>.scope) right after it is spawned.
"""

from __future__ import annotations

import logging
import os
import re

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GioUnix", "2.0")
from gi.repository import Gio, GioUnix, GLib  # noqa: E402

log = logging.getLogger(__name__)

_UNIT_SAFE = re.compile(r"[A-Za-z0-9:_.]")


def scope_name(app_id: str, pid: int) -> str:
    """systemd unit name following the XDG convention app-<launcher>-<app id>-<n>.scope."""
    base = app_id.removesuffix(".desktop")
    escaped = "".join(
        ch if _UNIT_SAFE.fullmatch(ch) else "".join(f"\\x{b:02x}" for b in ch.encode())
        for ch in base
    )
    return f"app-launcher-{escaped}-{pid}.scope"


def launch_app(app_id: str, uris: list[str], context: Gio.AppLaunchContext | None) -> None:
    info = GioUnix.DesktopAppInfo.new(app_id)
    if info is None:
        raise LookupError(f"application {app_id!r} is not installed")
    _launch(info, uris, context)


def open_uri(uri: str, context: Gio.AppLaunchContext | None) -> None:
    scheme = GLib.Uri.peek_scheme(uri) or "file"
    handler = Gio.AppInfo.get_default_for_uri_scheme(scheme)
    if isinstance(handler, GioUnix.DesktopAppInfo):
        _launch(handler, [uri], context)
    else:
        # No desktop-file handler to spawn ourselves; let GIO pick one.
        Gio.AppInfo.launch_default_for_uri(uri, context)


def open_file(path: str, context: Gio.AppLaunchContext | None) -> None:
    """Open a file or folder with the default app for its type."""
    gfile = Gio.File.new_for_path(path)
    info = gfile.query_info(Gio.FILE_ATTRIBUTE_STANDARD_CONTENT_TYPE, Gio.FileQueryInfoFlags.NONE)
    content_type = info.get_content_type() or "application/octet-stream"
    handler = Gio.AppInfo.get_default_for_type(content_type, False)
    if isinstance(handler, GioUnix.DesktopAppInfo):
        _launch(handler, [gfile.get_uri()], context)
    elif handler is not None:
        handler.launch([gfile], context)
    else:
        raise LookupError(f"no application is set to open {content_type} files")


def reveal_file(path: str, startup_id: str) -> None:
    """Show the file selected in its folder (Files / any FileManager1 implementation)."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.call(
        "org.freedesktop.FileManager1",
        "/org/freedesktop/FileManager1",
        "org.freedesktop.FileManager1",
        "ShowItems",
        GLib.Variant("(ass)", ([Gio.File.new_for_path(path).get_uri()], startup_id)),
        None,
        Gio.DBusCallFlags.NONE,
        5000,
        None,
        _on_reveal_done,
        path,
    )


def _on_reveal_done(bus: Gio.DBusConnection, result: Gio.AsyncResult, path: str) -> None:
    try:
        bus.call_finish(result)
    except GLib.Error as e:
        log.warning("cannot show %s in the file manager: %s", path, e.message)


def spawn(argv: list[str], unit_id: str, activation_token: str | None) -> None:
    """Start a helper process (e.g. Launcher Settings) outside launcher.service."""
    env = dict(os.environ)
    if activation_token:
        env["XDG_ACTIVATION_TOKEN"] = activation_token
        env["DESKTOP_STARTUP_ID"] = activation_token
    pid, *_ = GLib.spawn_async(
        argv,
        envp=[f"{k}={v}" for k, v in env.items()],
        flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
    )
    _adopt(unit_id, pid)


def _launch(info: GioUnix.DesktopAppInfo, uris: list[str], context) -> None:
    app_id = info.get_id() or "unknown"
    log.info("launching %s %s", app_id, " ".join(uris))
    # DO_NOT_REAP_CHILD so the pid stays valid until it has been moved to its scope;
    # a child watch reaps it afterwards. DBusActivatable apps are started by the session
    # bus instead and never reach the callback.
    # stdio goes to /dev/null: otherwise every app's console output would end up in the
    # launcher's own journal.
    devnull = os.open(os.devnull, os.O_RDWR)
    try:
        info.launch_uris_as_manager_with_fds(
            uris,
            context,
            GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
            None,
            None,
            lambda _info, pid, _data: _adopt(app_id, pid),
            None,
            devnull,
            devnull,
            devnull,
        )
    finally:
        os.close(devnull)


def _adopt(app_id: str, pid: int) -> None:
    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, lambda *_: None)
    name = scope_name(app_id, pid)
    properties = [
        ("PIDs", GLib.Variant("au", [pid])),
        ("CollectMode", GLib.Variant("s", "inactive-or-failed")),
        ("Description", GLib.Variant("s", f"Application launched by launcher: {app_id}")),
    ]
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as e:
        log.warning("cannot move %s (pid %d) to its own scope: %s", app_id, pid, e.message)
        return
    bus.call(
        "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager",
        "StartTransientUnit",
        GLib.Variant("(ssa(sv)a(sa(sv)))", (name, "fail", properties, [])),
        None,
        Gio.DBusCallFlags.NONE,
        -1,
        None,
        _on_scope_started,
        (app_id, pid, name),
    )


def _on_scope_started(bus: Gio.DBusConnection, result: Gio.AsyncResult, data) -> None:
    app_id, pid, name = data
    try:
        bus.call_finish(result)
        log.debug("moved %s (pid %d) to %s", app_id, pid, name)
    except GLib.Error as e:
        # The app still runs; it just stays in launcher.service and dies with it.
        log.warning("cannot move %s (pid %d) to %s: %s", app_id, pid, name, e.message)
