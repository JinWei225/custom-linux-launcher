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
from gi.repository import Adw, Gio, GLib  # noqa: E402

from .. import NOTES_APP_ID  # noqa: E402

log = logging.getLogger(__name__)


class NotesApp(Adw.Application):
    """Once open, Notes keeps running with its window hidden, so it comes back at once.

    A running Notes is driven through app actions (`launcher --notes` calls them over
    D-Bus without starting Python's GTK, see client.py): "toggle" shows the window, or
    hides it if it is the focused one (Super+Shift+N); "open-note" and "new-note". A
    first call starts it through the command line instead. Ctrl+Q quits.
    """

    def __init__(self) -> None:
        super().__init__(
            application_id=NOTES_APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        for name, ptype, handler in (
            ("toggle", None, lambda _p: self.window().toggle()),
            ("open-note", "s", lambda p: self._show().open_note(p.get_string())),
            ("new-note", "s", lambda p: self._show().new_note(text=p.get_string())),
            ("quit", None, lambda _p: self.quit_notes()),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
            action.connect("activate", lambda _a, p, h=handler: h(p))
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def window(self):
        from .window import NotesWindow

        windows = self.get_windows()
        return windows[0] if windows else NotesWindow(self)

    def _show(self):
        window = self.window()
        window.bring_to_front()
        return window

    def quit_notes(self) -> None:
        for window in self.get_windows():
            window.shut_down()
        self.quit()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        args = command_line.get_arguments()[1:]

        def value(flag: str) -> str | None:
            return args[args.index(flag) + 1] if flag in args[:-1] else None

        window = self._show()
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
