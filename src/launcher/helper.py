"""Client for the Launcher Helper GNOME Shell extension (extension/launcher-helper@…).

The extension runs inside GNOME Shell and exports io.github.jinwei.LauncherHelper on
the Shell's bus name. It only answers the process that owns the launcher's bus name,
i.e. this daemon.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

log = logging.getLogger(__name__)

DEST = "org.gnome.Shell"
PATH = "/io/github/jinwei/LauncherHelper"
IFACE = "io.github.jinwei.LauncherHelper"
EXTENSION_UUID = "launcher-helper@jinwei.github.io"


@dataclass(frozen=True)
class Target:
    """The window the launcher was opened from, to paste into."""

    window_id: int
    wm_class: str
    app_id: str


def real_app_id(app_id: str) -> str:
    """GNOME names windows without a .desktop file "window:<n>"; that isn't an app id."""
    return "" if app_id.startswith("window:") else app_id


def _bytes_variant(mime: str, data: bytes) -> GLib.Variant:
    # Built from GLib.Bytes: GLib.Variant("ay", data) would create one Python int per byte.
    payload = GLib.Variant.new_from_bytes(GLib.VariantType("ay"), GLib.Bytes.new(data), True)
    return GLib.Variant.new_tuple(GLib.Variant.new_string(mime), payload)


class Helper:
    def __init__(self) -> None:
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.available = False
        self.version = 0
        self._on_available: list[Callable[[bool], None]] = []
        # Re-check whenever GNOME Shell (re)starts or the extension is toggled.
        self._watch = Gio.bus_watch_name_on_connection(
            self._bus, DEST, Gio.BusNameWatcherFlags.NONE,
            lambda *_: self.refresh(), lambda *_: self._set_available(False),
        )  # fmt: skip

    def on_availability(self, callback: Callable[[bool], None]) -> None:
        self._on_available.append(callback)

    def refresh(self) -> None:
        self._bus.call(
            DEST, PATH, IFACE, "GetVersion", None, GLib.VariantType("(u)"),
            Gio.DBusCallFlags.NONE, 2000, None, self._on_version,
        )  # fmt: skip

    def _on_version(self, bus, result) -> None:
        try:
            self.version = bus.call_finish(result).unpack()[0]
            log.info("Launcher Helper extension v%d is active", self.version)
            self._set_available(True)
        except GLib.Error:
            self._set_available(False)

    def _set_available(self, available: bool) -> None:
        if available == self.available:
            return
        self.available = available
        if not available:
            log.warning("Launcher Helper extension is not active: no clipboard history or paste")
        for callback in self._on_available:
            callback(available)

    def subscribe_clipboard(self, callback: Callable[[list[str], str, str], None]) -> None:
        """callback(mimetypes, wm_class, app_id) whenever something is copied."""
        self._bus.signal_subscribe(
            DEST, IFACE, "ClipboardChanged", PATH, None, Gio.DBusSignalFlags.NONE,
            lambda _c, _s, _p, _i, _n, params: self._clipboard_changed(callback, params),
        )  # fmt: skip

    @staticmethod
    def _clipboard_changed(callback, params: GLib.Variant) -> None:
        mimetypes, wm_class, app_id = params.unpack()
        callback(mimetypes, wm_class, real_app_id(app_id))

    def focused_window(self) -> Target | None:
        if not self.available:
            return None
        try:
            result = self._bus.call_sync(
                DEST, PATH, IFACE, "GetFocusedWindow", None, GLib.VariantType("(uss)"),
                Gio.DBusCallFlags.NONE, 300, None,
            )  # fmt: skip
        except GLib.Error as e:
            log.warning("cannot ask for the focused window: %s", e.message)
            return None
        window_id, wm_class, app_id = result.unpack()
        return Target(window_id, wm_class, real_app_id(app_id)) if window_id else None

    def get_clipboard(self, mime: str, callback: Callable[[bytes | None], None]) -> None:
        def done(bus, result) -> None:
            try:
                value = bus.call_finish(result).get_child_value(0)
                callback(value.get_data_as_bytes().get_data())
            except GLib.Error as e:
                log.warning("cannot read the clipboard (%s): %s", mime, e.message)
                callback(None)

        self._bus.call(
            DEST, PATH, IFACE, "GetClipboard", GLib.Variant("(s)", (mime,)),
            GLib.VariantType("(ay)"), Gio.DBusCallFlags.NONE, 10000, None, done,
        )  # fmt: skip

    def set_clipboard(self, mime: str, data: bytes) -> bool:
        try:
            self._bus.call_sync(
                DEST, PATH, IFACE, "SetClipboard", _bytes_variant(mime, data), None,
                Gio.DBusCallFlags.NONE, 10000, None,
            )  # fmt: skip
            return True
        except GLib.Error as e:
            log.warning("cannot set the clipboard: %s", e.message)
            return False

    def paste(self, target: Target, with_shift: bool, callback: Callable[[bool], None]) -> None:
        def done(bus, result) -> None:
            try:
                callback(bus.call_finish(result).unpack()[0])
            except GLib.Error as e:
                log.warning("paste failed: %s", e.message)
                callback(False)

        self._bus.call(
            DEST, PATH, IFACE, "Paste", GLib.Variant("(ub)", (target.window_id, with_shift)),
            GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 3000, None, done,
        )  # fmt: skip

    def cursor_left(self, count: int) -> None:
        """Press Left `count` times in the focused window (extension v2+)."""
        self._bus.call(
            DEST, PATH, IFACE, "MoveCursorLeft", GLib.Variant("(u)", (count,)), None,
            Gio.DBusCallFlags.NONE, 3000, None, None,
        )  # fmt: skip
