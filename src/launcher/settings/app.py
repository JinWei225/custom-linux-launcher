"""Entry point for `launcher --settings`: its own single-instance application.

It runs in a separate process from the launcher daemon, so a problem in the settings UI
can never take the launcher down. A second `launcher --settings --edit …` is forwarded
to the open window.
"""

from __future__ import annotations

import logging
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from .. import APP_ID  # noqa: E402

log = logging.getLogger(__name__)

SETTINGS_APP_ID = APP_ID + ".Settings"


class SettingsApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=SETTINGS_APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        from .window import SettingsWindow

        args = command_line.get_arguments()[1:]

        def value(flag: str) -> str | None:
            return args[args.index(flag) + 1] if flag in args[:-1] else None

        window = self.get_active_window()
        if window is None:
            window = SettingsWindow(self)
        window.present()
        if edit := value("--edit"):
            window.open_item(edit)
        if snapshots := value("--debug-snapshots"):
            from .debug import render_all

            render_all(window, snapshots)
        return 0


def run_settings(edit: str | None, snapshots: str | None = None) -> int:
    argv = [sys.argv[0]]
    if edit:
        argv += ["--edit", edit]
    if snapshots:
        argv += ["--debug-snapshots", snapshots]
    return SettingsApp().run(argv)
