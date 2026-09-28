"""Entry point for `launcher --notes`: its own single-instance application.

Like Launcher Settings it runs in a separate process from the launcher daemon, so a
problem in the editor can never take the launcher down. A second `launcher --notes`
(the Super+Shift+N shortcut) brings the open window to the front.
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

NOTES_APP_ID = APP_ID + ".Notes"


class NotesApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=NOTES_APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        from .window import NotesWindow

        args = command_line.get_arguments()[1:]

        def value(flag: str) -> str | None:
            return args[args.index(flag) + 1] if flag in args[:-1] else None

        window = self.get_active_window()
        if window is None:
            window = NotesWindow(self)
        window.present()
        if note := value("--open"):
            window.open_note(note)
        elif "--new" in args:
            window.new_note(text=value("--new") or "")
        return 0


def run_notes(open_note: str | None = None, new: str | None = None) -> int:
    argv = [sys.argv[0]]
    if open_note:
        argv += ["--open", open_note]
    if new is not None:
        argv += ["--new", new]
    return NotesApp().run(argv)
