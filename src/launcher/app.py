"""The long-running primary instance: owns the window, config and providers."""

from __future__ import annotations

import logging
import signal
import sys
from importlib import resources

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, paths  # noqa: E402
from .config import Config, ConfigError, ensure_config, load_config  # noqa: E402
from .engine import MODES, Engine  # noqa: E402
from .providers.commands import CommandsProvider  # noqa: E402
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
        self._reload_source = 0

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

        engine = Engine({"commands": CommandsProvider(self)})
        self.window = LauncherWindow(self, engine, self.config)
        self.window.set_problems(problems)
        self.window.realize()  # pay the first-show setup cost at startup, not on first use
        self._add_actions()
        self._watch_config()
        log.info("launcher started (config: %s)", paths.config_file())

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
            ("show", "s", lambda p: self.window.show_mode(self._mode(p))),
            ("toggle", "s", lambda p: self.window.toggle_mode(self._mode(p))),
            ("hide", None, lambda p: self.window.hide_launcher()),
            ("reload", None, lambda p: self.reload_config()),
            ("open-config", None, lambda p: self.open_config()),
            ("quit", None, lambda p: self.quit_launcher()),
        ]
        if self._debug:
            actions += [
                ("debug-snapshot", "s", lambda p: self.window.save_snapshot(p.get_string())),
                ("debug-set-query", "s", lambda p: self.window.set_query(p.get_string())),
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
        self.window.apply_config(config)
        self.window.set_problems(warnings)
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
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, lambda: (app.quit(), True)[1])
    return app.run(sys.argv[:1])
