"""The Notes editor as CodeMirror 6 in a WebKit view: a prototype, chosen with
LAUNCHER_NOTES_EDITOR=web in place of the Gtk.TextView one (editor.py).

It offers the window what MarkdownEditor does (load_text, text, headings, cursor_line,
go_to_line, pictures, links, a "text-changed" signal). The formatting and list editing
happen in the page (tools/codemirror/src, built into web/editor.js); every change is
sent here, so text() and cursor_line() answer at once from a copy, without waiting on
the page. Needs gir1.2-webkit-6.0.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango, WebKit  # noqa: E402

from .. import notes_markdown as md  # noqa: E402
from .editor import offers_picture, save_picture  # noqa: E402

log = logging.getLogger(__name__)

PAGE = Path(__file__).parent / "web" / "editor.html"
FONT_SIZE = 13  # pt, as the GTK editor
MENU_ACTIONS = {  # what the right-click menu keeps (no Reload, Back, Open Link...)
    WebKit.ContextMenuAction.CUT,
    WebKit.ContextMenuAction.COPY,
    WebKit.ContextMenuAction.PASTE,
    WebKit.ContextMenuAction.PASTE_AS_PLAIN_TEXT,
    WebKit.ContextMenuAction.DELETE,
    WebKit.ContextMenuAction.SELECT_ALL,
    WebKit.ContextMenuAction.INSPECT_ELEMENT,
}


class WebMarkdownEditor(Gtk.Box):
    __gsignals__ = {"text-changed": (GObject.SignalFlags.RUN_FIRST, None, ())}
    scrolls_itself = True  # the page scrolls: no Gtk.ScrolledWindow around it

    def __init__(self) -> None:
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("view")
        self.note_stem = "image"
        self.link_handler: Callable[[str], None] | None = None
        self.error_handler: Callable[[str], None] | None = None
        self._base_dir: Path | None = None
        self._text = ""
        self._line = 0
        self._offset = 0
        self._loaded = False
        self._ready = False
        self._pending: list[str] = []  # calls made before the page was ready

        manager = WebKit.UserContentManager()
        manager.register_script_message_handler("notes", None)
        manager.connect("script-message-received::notes", self._on_message)
        self.view = WebKit.WebView(user_content_manager=manager, hexpand=True, vexpand=True)
        self.view.set_background_color(Gdk.RGBA(0, 0, 0, 0))  # the GTK view's, no flash
        settings = self.view.get_settings()
        settings.set_allow_file_access_from_file_urls(True)  # the note's pictures
        settings.set_enable_developer_extras(bool(os.environ.get("LAUNCHER_NOTES_INSPECT")))
        self.view.connect("decide-policy", self._on_policy)
        self.view.connect("context-menu", self._on_context_menu)
        self.view.connect("web-process-terminated", self._on_crash)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.view.add_controller(keys)
        self.append(self.view)

        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_: self._send_style())
        style.connect("notify::accent-color-rgba", lambda *_: self._send_style())
        self.view.load_uri(PAGE.as_uri())

    # --- the page ------------------------------------------------------------------------

    def _call(self, name: str, *args) -> None:
        script = f"notes.{name}({', '.join(json.dumps(a) for a in args)})"
        if not self._ready:
            self._pending.append(script)
            return
        self.view.evaluate_javascript(script, -1, None, None, None, None, None)

    def _on_message(self, _manager, value) -> None:
        message = json.loads(value.to_string())
        kind = message["type"]
        if kind == "ready":
            self._ready = True
            pending, self._pending = self._pending, []
            self._send_style()
            self._call("setBaseDir", self._base_uri())
            if pending:
                for script in pending:
                    self.view.evaluate_javascript(script, -1, None, None, None, None, None)
            elif self._loaded:  # the page was reloaded (its process crashed): put the note back
                self._call("load", self._text, self._offset)
        elif kind == "changed":
            self._text = message["text"]
            self._line, self._offset = message["line"], message["offset"]
            self.emit("text-changed")
        elif kind == "cursor":
            self._line, self._offset = message["line"], message["offset"]
        elif kind == "link" and self.link_handler is not None:
            self.link_handler(message["url"])

    def _send_style(self) -> None:
        style = Adw.StyleManager.get_default()
        font = Pango.FontDescription.from_string(
            Gtk.Settings.get_default().get_property("gtk-font-name") or "Sans"
        )
        accent = style.get_accent_color_rgba()
        self._call(
            "setStyle",
            {
                "dark": style.get_dark(),
                "accent": accent.to_string() if accent else None,
                "font": font.get_family(),
                "size": FONT_SIZE,
            },
        )

    def _on_policy(self, _view, decision, decision_type) -> bool:
        """The page never navigates away (a dropped file, a clicked link): links open
        through the window, like Ctrl+click."""
        if decision_type == WebKit.PolicyDecisionType.RESPONSE:
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION and uri == PAGE.as_uri():
            return False
        decision.ignore()
        return True

    def _on_context_menu(self, _view, menu, _hit) -> bool:
        for item in list(menu.get_items()):
            if item.get_stock_action() not in MENU_ACTIONS:
                menu.remove(item)
        return menu.get_n_items() == 0  # True: no menu at all

    def _on_crash(self, _view, reason) -> None:
        log.warning("notes editor page ended (%s); reloading it", reason)
        self._ready = False
        self.view.load_uri(PAGE.as_uri())

    # --- what the window uses ------------------------------------------------------------

    def load_text(self, text: str) -> None:
        """Show a note's text as it is (no renumbering, no rewrites), not undoable."""
        self._text, self._line, self._offset, self._loaded = text, 0, 0, True
        self._call("load", text, 0)

    def text(self) -> str:
        return self._text

    def headings(self) -> list[md.Heading]:
        return md.outline(self._text.split("\n"))

    def cursor_line(self) -> int:
        return self._line

    def cursor_offset(self) -> int:
        return self._offset

    def place_cursor(self, offset: int) -> None:
        """Put the cursor at an offset (-1: the end)."""
        self._offset = len(self._text) if offset < 0 else min(offset, len(self._text))
        self._call("setCursor", offset)

    def go_to_line(self, line: int) -> None:
        """Put the cursor at the start of a line's text and scroll that line to the top."""
        self._line = line
        self.view.grab_focus()
        self._call("goToLine", line)

    def grab_focus(self) -> bool:
        self._call("focus")
        return self.view.grab_focus()

    @property
    def base_dir(self) -> Path | None:
        return self._base_dir

    @base_dir.setter
    def base_dir(self, path: Path | None) -> None:
        if path != self._base_dir:
            self._base_dir = path
            self._call("setBaseDir", self._base_uri())

    def _base_uri(self) -> str | None:
        return self._base_dir.as_uri() + "/" if self._base_dir is not None else None

    def image_path(self, url: str) -> Path | None:
        """A picture's file, for a path relative to the note (not for web images)."""
        if self._base_dir is None or "://" in url or url.startswith(("www.", "mailto:")):
            return None
        path = Path(unquote(url))
        return path if path.is_absolute() else self._base_dir / path

    # --- pasting pictures ----------------------------------------------------------------

    def _on_key(self, _ctrl, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        """Ctrl+V of a picture (a screenshot): save it next to the note and link it.
        Anything with text in it is left to the page."""
        mods = state & Gtk.accelerator_get_default_mod_mask()
        ctrl_v = mods == Gdk.ModifierType.CONTROL_MASK and Gdk.keyval_to_lower(keyval) == Gdk.KEY_v
        shift_insert = mods == Gdk.ModifierType.SHIFT_MASK and keyval == Gdk.KEY_Insert
        if not (ctrl_v or shift_insert):
            return False
        clipboard = self.get_clipboard()
        if not offers_picture(clipboard.get_formats()):
            return False
        clipboard.read_texture_async(None, self._on_pasted_texture)
        return True

    def _on_pasted_texture(self, clipboard: Gdk.Clipboard, result) -> None:
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error as e:
            self._error(f"Could not paste the picture: {e.message}")
            return
        if texture is None:
            return
        try:
            link = save_picture(self._base_dir, self.note_stem, texture)
        except OSError as e:
            self._error(f"Could not save the picture: {e}")
            return
        self._call("insertPictureLink", link)

    def _error(self, message: str) -> None:
        log.error("%s", message)
        if self.error_handler is not None:
            self.error_handler(message)
