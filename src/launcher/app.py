"""The long-running primary instance: owns the window, config and providers."""

from __future__ import annotations

import functools
import logging
import signal
import sys
import time
from importlib import resources

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GLibUnix", "2.0")
gi.require_version("GioUnix", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, GLibUnix, Gtk  # noqa: E402

from . import APP_ID, launching, paths, shortcuts  # noqa: E402
from .clipboard_recorder import ClipboardRecorder  # noqa: E402
from .clipboard_store import ClipboardStore, app_matches  # noqa: E402
from .config import Config, ConfigError, ensure_config, hotkey_owners, load_config  # noqa: E402
from .engine import MODES, Engine  # noqa: E402
from .favicons import FaviconCache  # noqa: E402
from .file_watcher import FileWatcher  # noqa: E402
from .files_index import FileIndex, IndexSettings  # noqa: E402
from .helper import Helper, Target  # noqa: E402
from .providers.apps import AppsProvider  # noqa: E402
from .providers.base import Result  # noqa: E402
from .providers.clipboard import ClipboardProvider  # noqa: E402
from .providers.commands import CommandsProvider  # noqa: E402
from .providers.files import FilesProvider  # noqa: E402
from .providers.quicklinks import (  # noqa: E402
    QuickLinksProvider,
    WebSearchProvider,
    fill_url,  # noqa: E402
)
from .store import UsageStore  # noqa: E402
from .window import LauncherWindow  # noqa: E402

log = logging.getLogger(__name__)

CONFIG_RELOAD_DELAY_MS = 200


class LauncherApp(Adw.Application):
    def __init__(self, initial: tuple[str, str | None] | None, debug: bool = False) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._initial = initial
        self._first_activate = True
        self._debug = debug
        self.config = Config()
        self.window: LauncherWindow | None = None
        self._config_monitor: Gio.FileMonitor | None = None
        self._app_monitor: Gio.AppInfoMonitor | None = None
        self._reload_source = 0
        self._usage: UsageStore | None = None
        self._favicons: FaviconCache | None = None
        self._file_watcher: FileWatcher | None = None
        self._helper: Helper | None = None
        self._clips: ClipboardStore | None = None
        self._recorder: ClipboardRecorder | None = None
        self._paste_target: Target | None = None

    # --- lifecycle -------------------------------------------------------------------

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self.hold()  # keep running with the window hidden
        self._load_css()

        ensure_config(paths.config_file())
        problems: list[str] = []
        try:
            self.config, problems = load_config(paths.config_file())
        except ConfigError as e:
            problems = [f"{e} (using defaults)"]

        self._usage = UsageStore(paths.data_dir() / "launcher.db")
        self._favicons = FaviconCache(
            paths.cache_dir() / "favicons",
            on_update=lambda: self.window and self.window.refresh_if_visible(),
            call_soon=lambda fn: GLib.idle_add(lambda: (fn(), GLib.SOURCE_REMOVE)[1]),
        )
        apps = AppsProvider(self)
        file_index = FileIndex()
        self._file_watcher = FileWatcher(file_index)
        self._helper = Helper()
        self._clips = ClipboardStore(paths.data_dir())
        self._clips.prune(self.config.clipboard.max_entries, self.config.clipboard.max_days)
        self._recorder = ClipboardRecorder(
            self._helper,
            self._clips,
            lambda: self.config.clipboard,
            on_added=lambda: self.window and self.window.refresh_if_visible(),
        )
        self._helper.on_availability(lambda _ok: self.window and self.window.refresh_layout())
        self._engine = Engine(
            {
                "apps": apps,
                "quicklinks": QuickLinksProvider(self, icons=self._website_icon),
                "commands": CommandsProvider(self),
                "websearch": WebSearchProvider(self, icons=self._website_icon),
                "files": FilesProvider(self, file_index),
                "clipboard": ClipboardProvider(self, self._clips, app_name=_app_name),
            },
            usage=self._usage,
        )
        self._engine.configure(self.config)
        self._prefetch_icons()
        self._file_watcher.configure(IndexSettings.from_config(self.config.files))
        # Rebuild the app list lazily whenever .desktop files are added or removed.
        self._app_monitor = Gio.AppInfoMonitor.get()
        self._app_monitor.connect("changed", lambda _m: apps.invalidate())
        apps.query("")  # load the app list now rather than on the first keystroke

        self.window = LauncherWindow(self, self._engine, self.config)
        self.window.set_problems(problems + self._sync_shortcuts())
        self.window.realize()  # pay the first-show setup cost at startup, not on first use
        self._add_actions()
        self._watch_config()
        log.info("launcher started (config: %s)", paths.config_file())

    def do_shutdown(self) -> None:
        if self._clips is not None:
            self._clips.close()
        if self._usage is not None:
            self._usage.close()
        if self._favicons is not None:
            self._favicons.shutdown()
        if self._file_watcher is not None:
            self._file_watcher.shutdown()
        Adw.Application.do_shutdown(self)

    def do_activate(self) -> None:
        # First activation comes from our own run(); later ones from e.g. `gapplication launch`.
        if self._first_activate:
            self._first_activate = False
            if self._initial is None:
                return
            name, param = self._initial
            self.activate_action(name, GLib.Variant("s", param) if param else None)
        else:
            self.activate_action("show", GLib.Variant("s", "all"))

    def do_before_emit(self, platform_data: GLib.Variant) -> None:
        # GtkApplication picks the activation token out of platform_data here; the
        # override only adds logging so focus problems can be diagnosed.
        has_token = "activation-token" in platform_data.keys()
        log.debug("remote call, activation token: %s", "yes" if has_token else "no")
        Adw.Application.do_before_emit(self, platform_data)

    # --- actions ---------------------------------------------------------------------

    def _add_actions(self) -> None:
        actions = [
            ("show", "s", lambda p: self._show(self._mode(p), toggle=False)),
            ("toggle", "s", lambda p: self._show(self._mode(p), toggle=True)),
            ("toggle-clipboard-pause", None, lambda p: self.toggle_clipboard_pause()),
            # Seconds of recent history to delete (pinned entries kept); 0 = everything.
            ("clear-clipboard", "x", lambda p: self.clear_clipboard(p.get_int64())),
            ("hide", None, lambda p: self.window.hide_launcher()),
            ("reload", None, lambda p: self.reload_config()),
            ("open-config", None, lambda p: self.open_config()),
            ("quit", None, lambda p: self.quit_launcher()),
            ("run", "s", lambda p: self.run_item(p.get_string())),
        ]
        if self._debug:
            actions += [
                ("debug-snapshot", "s", lambda p: self.window.save_snapshot(p.get_string())),
                ("debug-set-query", "s", lambda p: self.window.set_query(p.get_string())),
                ("debug-run-selected", None, lambda p: self.window.run_selected()),
                ("debug-run-selected-alt", None, lambda p: self.window.run_selected(alt=True)),
            ]
        for name, ptype, handler in actions:
            action = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
            action.connect("activate", lambda _a, p, h=handler: h(p))
            self.add_action(action)

    @staticmethod
    def _mode(param: GLib.Variant) -> str:
        mode = param.get_string()
        if mode not in MODES:
            log.warning("unknown mode %r, using 'all'", mode)
            return "all"
        return mode

    # --- Host interface used by providers --------------------------------------------

    def reload_config(self) -> None:
        try:
            config, warnings = load_config(paths.config_file())
        except ConfigError as e:
            log.error("config error: %s", e)
            self.window.set_problems([f"{e} (keeping previous settings)"])
            return
        for w in warnings:
            log.warning("config: %s", w)
        self.config = config
        self._engine.configure(config)
        self._prefetch_icons()
        self._file_watcher.configure(IndexSettings.from_config(config.files))
        self.window.apply_config(config)
        self.window.set_problems(warnings + self._sync_shortcuts())
        log.info("config reloaded")

    def open_config(self) -> None:
        path = paths.config_file()
        ensure_config(path)
        context = Gdk.Display.get_default().get_app_launch_context()
        try:
            Gio.AppInfo.launch_default_for_uri(path.as_uri(), context)
        except GLib.Error:
            # No handler registered for TOML: fall back to the default text editor.
            editor = Gio.AppInfo.get_default_for_type("text/plain", False)
            if editor is None:
                self.notify_error("Cannot open config", f"No text editor found for {path}")
                return
            editor.launch([Gio.File.new_for_path(str(path))], context)

    def quit_launcher(self) -> None:
        log.info("quit requested")
        self.quit()

    def launch_app(self, app_id: str) -> None:
        launching.launch_app(app_id, [], self._launch_context())

    def open_uri(self, uri: str) -> None:
        launching.open_uri(uri, self._launch_context())

    def copy_text(self, text: str) -> None:
        Gdk.Display.get_default().get_clipboard().set(text)

    def open_file(self, path: str) -> None:
        launching.open_file(path, self._launch_context())

    def reveal_file(self, path: str) -> None:
        # The file manager needs an activation token to raise its window.
        token = self._launch_context().get_startup_notify_id(None, []) or ""
        launching.reveal_file(path, token)

    def _website_icon(self, url: str) -> str | None:
        if not self.config.ui.favicons or self._favicons is None:
            return None
        return self._favicons.icon_for(url)

    def _prefetch_icons(self) -> None:
        for link in self.config.quicklinks:
            if not link.icon:
                self._website_icon(link.url)

    @staticmethod
    def _launch_context() -> Gio.AppLaunchContext:
        # Carries an xdg-activation token, so GNOME focuses the launched app. The token
        # is only valid while our window is focused: run actions before hiding it.
        return Gdk.Display.get_default().get_app_launch_context()

    def open_settings(self, edit: str | None = None) -> None:
        argv = [sys.executable, "-m", "launcher", "--settings"]
        if edit:
            argv += ["--edit", edit]
        token = self._launch_context().get_startup_notify_id(None, []) or None
        launching.spawn(argv, APP_ID + ".Settings", token)

    def run_item(self, item_id: str) -> None:
        """What a per-item hotkey runs: `launcher --run app:<id>` / `quicklink:<name>`."""
        kind, _, key = item_id.partition(":")
        try:
            if kind == "app":
                self.launch_app(key)
            elif kind == "quicklink":
                link = next(
                    (q for q in self.config.quicklinks if q.name.casefold() == key.casefold()),
                    None,
                )
                if link is None:
                    raise LookupError(f"there is no quicklink named '{key}' any more")
                if "{query}" in link.url and link.alias:
                    # A search: open the launcher with "g " typed, ready for the query.
                    self.window.show_mode("all")
                    self.window.set_query(f"{link.alias} ")
                    return
                self.open_uri(fill_url(link.url, ""))
            else:
                raise LookupError(f"unknown item '{item_id}'")
        except Exception as e:
            self.notify_error("Launcher hotkey failed", str(e))
            return
        self._engine.record(Result(id=item_id, title=key))

    def _sync_shortcuts(self) -> list[str]:
        """Register the config's hotkeys with GNOME; returns problems for the banner."""
        problems = []
        for key, owner in hotkey_owners(self.config):
            ok, keyval, _mods = Gtk.accelerator_parse(key)
            if not ok or keyval == 0:
                problems.append(f"{owner}: '{key}' is not a key GNOME understands")
        if problems:
            return problems  # don't half-apply a config with a broken hotkey
        try:
            return shortcuts.sync(self.config)
        except Exception as e:
            log.exception("syncing shortcuts failed")
            return [f"could not register shortcuts with GNOME: {e}"]

    # --- clipboard ---------------------------------------------------------------------

    def _show(self, mode: str, toggle: bool) -> None:
        if not (toggle and self.window.get_visible() and self.window.mode == mode):
            # Remember the window we are opened from: clipboard entries paste back into it.
            target = self._helper.focused_window()
            if target is not None and not app_matches((APP_ID,), target.wm_class, target.app_id):
                self._paste_target = target
                log.debug("paste target: %s", target)
        if toggle:
            self.window.toggle_mode(mode)
        else:
            self.window.show_mode(mode)

    def mode_status(self, mode: str) -> str | None:
        """A line shown under the search box in this mode, if something needs attention."""
        if mode != "clipboard" or self._helper is None:
            return None
        if not self._helper.available:
            return (
                "Clipboard history needs the Launcher Helper GNOME extension: run "
                "`make install-extension`, then log out and back in."
            )
        if not self.config.clipboard.enabled:
            return "Clipboard history is turned off ([clipboard] enabled = false)."
        if self._recorder.paused:
            key = self.config.shortcuts.clipboard_pause
            return "Recording is paused" + (f" (press {key_label(key)} to resume)." if key else ".")
        return None

    def clipboard_paused(self) -> bool:
        return self._recorder is not None and self._recorder.paused

    def toggle_clipboard_pause(self) -> None:
        self._recorder.paused = not self._recorder.paused
        state = "paused" if self._recorder.paused else "resumed"
        log.info("clipboard recording %s", state)
        notification = Gio.Notification.new(f"Clipboard recording {state}")
        if self._recorder.paused:
            notification.set_body("Nothing you copy is saved until you resume.")
        self.send_notification("clipboard-pause", notification)
        self.window.refresh_layout()

    def paste_clip(self, clip_id: int) -> None:
        self._put_on_clipboard(clip_id)
        target = self._paste_target
        if target is None or not self._helper.available:
            self._notify("Copied to the clipboard", "Press Ctrl+V to paste it.")
            return
        with_shift = app_matches(
            self.config.clipboard.terminal_apps, target.wm_class, target.app_id
        )
        log.debug("pasting into %s (with shift: %s)", target, with_shift)

        def paste() -> bool:
            # By now the launcher window has hidden; the extension waits for focus to
            # return to the target window before sending the keys.
            self._helper.paste(target, with_shift, self._on_pasted)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(50, paste)

    def _on_pasted(self, pasted: bool) -> None:
        if not pasted:
            self._notify(
                "Copied to the clipboard",
                "The window it was meant for is gone; press Ctrl+V where you want it.",
            )

    def copy_clip(self, clip_id: int) -> None:
        self._put_on_clipboard(clip_id)

    def pin_clip(self, clip_id: int, pinned: bool) -> None:
        self._clips.set_pinned(clip_id, pinned)

    def delete_clip(self, clip_id: int) -> None:
        self._clips.delete(clip_id)

    def clear_clipboard(self, seconds: int = 0) -> None:
        since = time.time() - seconds if seconds > 0 else None
        removed = self._clips.clear(keep_pinned=True, since=since)
        span = describe_span(seconds)
        log.info("deleted %d clipboard entries (%s)", removed, span)
        entries = "1 entry" if removed == 1 else f"{removed} entries"
        self._notify("Clipboard history deleted", f"{entries} from {span} removed; pinned kept.")
        if self.window is not None:
            self.window.refresh_if_visible()

    def _put_on_clipboard(self, clip_id: int) -> None:
        content = self._clips.content(clip_id)
        if content is None:
            raise LookupError("this clipboard entry no longer exists")
        mime, data = content
        if self._helper.available and self._helper.set_clipboard(mime, data):
            return
        # Without the extension: our own window is still focused, so GTK may set it.
        provider = Gdk.ContentProvider.new_for_bytes(mime, GLib.Bytes.new(data))
        Gdk.Display.get_default().get_clipboard().set_content(provider)

    def _notify(self, title: str, body: str) -> None:
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        self.send_notification("clipboard", notification)

    def notify_error(self, title: str, body: str) -> None:
        log.error("%s: %s", title, body)
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        self.send_notification(None, notification)

    # --- internals -------------------------------------------------------------------

    def _load_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string(resources.files("launcher").joinpath("style.css").read_text())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _watch_config(self) -> None:
        # Watch the directory, not the file: editors often save by renaming a temp file.
        directory = Gio.File.new_for_path(str(paths.config_dir()))
        self._config_monitor = directory.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._config_monitor.connect("changed", self._on_config_dir_changed)

    def _on_config_dir_changed(self, _monitor, file, other, _event) -> None:
        name = paths.config_file().name
        if file.get_basename() != name and (other is None or other.get_basename() != name):
            return
        if self._reload_source:
            GLib.source_remove(self._reload_source)
        self._reload_source = GLib.timeout_add(CONFIG_RELOAD_DELAY_MS, self._reload_from_monitor)

    def _reload_from_monitor(self) -> bool:
        self._reload_source = 0
        self.reload_config()
        return GLib.SOURCE_REMOVE


@functools.lru_cache(maxsize=256)
def _app_name(source: str) -> str:
    """Display name for a clipboard source (desktop id or window class)."""
    info = launching.desktop_app(source if source.endswith(".desktop") else source + ".desktop")
    return info.get_name() if info is not None else source


def describe_span(seconds: int) -> str:
    if seconds <= 0:
        return "all time"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size and seconds % size == 0:
            n = seconds // size
            return f"the last {unit}" if n == 1 else f"the last {n} {unit}s"
    return f"the last {seconds} seconds"


def key_label(hotkey: str) -> str:
    ok, key, mods = Gtk.accelerator_parse(hotkey)
    return Gtk.accelerator_get_label(key, mods) if ok and key else hotkey


def run_primary(initial: tuple[str, str | None] | None, debug: bool = False) -> int:
    app = LauncherApp(initial, debug=debug)
    try:
        app.register(None)
    except GLib.Error as e:
        log.error("cannot register %s on the session bus: %s", APP_ID, e.message)
        return 1
    if app.get_is_remote():
        # Another instance won the race to start; forward the request to it.
        if initial is not None:
            name, param = initial
            app.activate_action(name, GLib.Variant("s", param) if param else None)
        return 0
    for signum in (signal.SIGINT, signal.SIGTERM):
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signum, lambda: (app.quit(), True)[1])
    return app.run(sys.argv[:1])
