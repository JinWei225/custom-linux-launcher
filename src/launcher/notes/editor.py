"""The Notes editor: a Gtk.TextView that formats markdown blocks as you type.

The buffer always holds the plain markdown. After every change each line is classified
(notes_markdown.classify) and only lines whose kind changed are re-tagged:

- headings get a bigger font; their "## " is dimmed on the cursor's line, hidden elsewhere
- list, checkbox and quote markers are always hidden; a bullet, checkbox, bar or list
  number is drawn in their place (snapshot_layer), and the lines are indented with tag
  margins. A list number is drawn as written (1. / b) / iv.) and can still be edited:
  Left at the start of the item's text (or a click on the number) shows it as text;
  Enter or going back to the text hides it again
- "---" is drawn as a line (its text shows on the cursor's line); code blocks get a
  monospace font on a rounded background

- inline **bold**, *italic*, ~~strike~~, `code`, ==highlight==, <u>underline</u> and
  [links](url): their markers are dimmed on the cursor's line and hidden elsewhere
- a line holding only an image, ![](attachments/x.png), shows the picture below it

Typing rules live in on_key: Enter continues a list (and ends it on an empty item), Tab /
Shift+Tab indent list items, Backspace at the start of an item or heading removes its
marker, Ctrl+Enter or a click toggles a checkbox, Ctrl+B/I/U/E, Ctrl+Shift+X/H and Ctrl+K
wrap the selection in a style or link. "[] " becomes a checkbox, and ordered lists
renumber themselves. Those edits happen inside the same user action as the keystroke,
so one Ctrl+Z undoes both. Pasting a picture saves it in attachments/ next to the note.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .. import notes_markdown as md  # noqa: E402

log = logging.getLogger(__name__)

MAX_TEXT_WIDTH = 820  # the text column; wider windows get side margins
MIN_MARGIN = 32
LIST_STEP = 28  # px per list level; the bullet or checkbox sits in the step before the text
NUMBER_GAP = 6  # between a list number (drawn right-aligned) and its text
QUOTE_STEP = 18
CODE_INSET = 14
CHECKBOX = 15
BULLETS = ("•", "◦", "▪")
HEADING_SCALES = {1: 1.6, 2: 1.35, 3: 1.15}
MAX_IMAGE_HEIGHT = 600
IMAGE_GAP = (4, 10)  # space above and below a picture
LINK_KINDS = ("link", "url", "image")
SHORTCUTS = {  # (key, with shift) -> style
    (Gdk.KEY_b, False): "bold",
    (Gdk.KEY_i, False): "italic",
    (Gdk.KEY_u, False): "underline",
    (Gdk.KEY_e, False): "code",
    (Gdk.KEY_x, True): "strike",
    (Gdk.KEY_h, True): "highlight",
}
_ENTER = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter)


def _rect(x: float, y: float, w: float, h: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, w, h)


def _offers_picture(formats: Gdk.ContentFormats) -> bool:
    """A clipboard holding a picture: image/* data from another app (a screenshot), or a
    texture object from this one (whose type is a subclass such as GdkMemoryTexture)."""
    if any(m.startswith("image/") for m in formats.get_mime_types() or ()):
        return True
    return any(GObject.type_is_a(t, Gdk.Texture.__gtype__) for t in formats.get_gtypes() or ())


def _rgba(spec: str) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.parse(spec)
    return color


class MarkdownEditor(Gtk.TextView):
    def __init__(self) -> None:
        super().__init__(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=24,
            bottom_margin=120,
            left_margin=MIN_MARGIN,
            right_margin=MIN_MARGIN,
            pixels_below_lines=6,
            pixels_inside_wrap=2,
        )
        self.add_css_class("notes-editor")
        self.buffer = self.get_buffer()
        self._margin = MIN_MARGIN
        self._pending_margin = 0
        self._lines: list[str] = []
        self._infos: list[md.LineInfo] = []
        self._cursor_line = -1
        self._loading = False  # load_text(): style only, never rewrite the file's text
        self._editing = False  # our own edits: don't react to them
        self._undoing = False  # undo/redo: don't re-apply renumbering on top
        self._preedit = False
        self._checkboxes: dict[int, Graphene.Rect] = {}  # line -> drawn box (buffer coords)
        self._bullets: dict[int, float] = {}  # line -> baseline the bullet was drawn on
        self._numbers: dict[int, tuple[str, float, float]] = {}  # line -> label, right x, baseline
        self._number_boxes: dict[int, Graphene.Rect] = {}  # line -> drawn number (buffer coords)
        self._number_line: int | None = None  # the numbered item whose "1. " is being edited
        self._blocks: list[tuple[int, int]] = []  # fenced code blocks: (first, last) line
        # Set by the window for the open note: where its pictures are, what to call new
        # ones, and what to do with Ctrl+clicked links and errors.
        self.base_dir: Path | None = None
        self.note_stem = "image"
        self.link_handler: Callable[[str], None] | None = None
        self.error_handler: Callable[[str], None] | None = None
        self._textures: dict[Path, tuple[float, Gdk.Texture | None]] = {}
        self._image_tags: dict[int, Gtk.TextTag] = {}
        self._column = MAX_TEXT_WIDTH
        self._create_tags()
        # Start of the line whose heading/rule markup is shown (the cursor's line). A mark
        # rather than a number, so it stays on that line when lines are added above it.
        self._shown = self.buffer.create_mark(None, self.buffer.get_start_iter(), True)

        self.buffer.connect("changed", self._on_changed)
        # Automatic edits (renumbering, "[] ") happen when the user's action ends: GTK
        # emits end-user-action before closing the undo step, so one Ctrl+Z undoes the
        # keystroke and its follow-up edits. Editing inside "changed" is unsafe (a
        # deletion is still in progress there).
        self.buffer.connect("end-user-action", self._on_user_action_done)
        self._touched: tuple[int, int] | None = None  # lines changed in this user action
        self.buffer.connect("notify::cursor-position", self._on_cursor_moved)
        self._cursor_idle = 0
        for signal in ("undo", "redo"):
            self.buffer.connect(signal, self._set_undoing, True)
            self.buffer.connect_after(signal, self._set_undoing, False)
        self.connect("preedit-changed", lambda _v, text: setattr(self, "_preedit", bool(text)))

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_click)
        self.add_controller(click)
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._on_query_tooltip)
        self.connect("paste-clipboard", self._on_paste)
        style = Adw.StyleManager.get_default()
        # Colours follow the theme; the new CSS applies a moment after the switch.
        style.connect("notify::dark", lambda *_a: GLib.idle_add(self._update_colors))
        style.connect("notify::accent-color", lambda *_a: GLib.idle_add(self._update_colors))
        self.connect("realize", lambda *_a: self._update_colors())
        self._update_colors()

    # --- tags --------------------------------------------------------------------------

    def _create_tags(self) -> None:
        b = self.buffer
        self._tags: list[Gtk.TextTag] = []

        def tag(name: str, **props) -> Gtk.TextTag:
            t = b.create_tag(name, **props)
            self._tags.append(t)
            return t

        self.t_heading = {
            level: tag(
                f"h{level}",
                scale=scale,
                weight=Pango.Weight.BOLD,
                pixels_above_lines=16 - 3 * level,
            )
            for level, scale in HEADING_SCALES.items()
        }
        # Hidden markup is shrunk to nothing and made transparent, not made "invisible":
        # GtkTextView's invisible text breaks its mapping between screen positions and
        # characters (clicks, hovering, an input method's preedit) and aborts the app with
        # "byte index off the end of the line". Tiny text stays in the layout, so every
        # position maps to a real character. Tabs get a 1 px stop, as a tab's width
        # doesn't follow its font size.
        tiny_tabs = Pango.TabArray.new(1, True)
        tiny_tabs.set_tab(0, Pango.TabAlign.LEFT, 1)
        self.t_hidden = tag(
            "md-hidden", size=1, foreground_rgba=_rgba("rgba(0,0,0,0)"), tabs=tiny_tabs
        )
        self.t_markup = tag("md-markup")  # dimmed markup; colour set in _update_colors
        self.t_list = [tag(f"md-list{d}") for d in range(md.MAX_DEPTH + 1)]
        self.t_quote = [tag(f"md-quote{d}", style=Pango.Style.ITALIC) for d in range(1, 7)]
        self.t_code = tag("md-code", family="monospace", scale=0.92)
        self.t_fence = tag("md-fence", family="monospace", scale=0.85)
        self.t_done = tag("md-done", strikethrough=True)
        self.t_inline = {
            "bold": tag("md-bold", weight=Pango.Weight.BOLD),
            "italic": tag("md-italic", style=Pango.Style.ITALIC),
            "strike": tag("md-strike", strikethrough=True),
            "code": tag("md-inline-code", family="monospace", scale=0.92),
            "highlight": tag("md-highlight"),
            "underline": tag("md-underline", underline=Pango.Underline.SINGLE),
            "link": tag("md-link", underline=Pango.Underline.SINGLE),
        }
        for kind in ("url", "image"):
            self.t_inline[kind] = self.t_inline["link"]
        # A line whose only text is a hidden image link: make its text row tiny.
        self.t_image_row = tag("md-image-row", size_points=1)
        self._apply_margins()

    def _apply_margins(self) -> None:
        m = self._margin
        for depth, t in enumerate(self.t_list):
            t.set_property("left-margin", m + (depth + 1) * LIST_STEP)
        for depth, t in enumerate(self.t_quote, 1):
            t.set_property("left-margin", m + depth * QUOTE_STEP)
        for t in (self.t_code, self.t_fence):
            t.set_property("left-margin", m + CODE_INSET)
            t.set_property("right-margin", m + CODE_INSET)

    def _update_colors(self) -> None:
        fg = self.get_color()
        dim = Gdk.RGBA()
        dim.red, dim.green, dim.blue, dim.alpha = fg.red, fg.green, fg.blue, 0.45
        soft = Gdk.RGBA()
        soft.red, soft.green, soft.blue, soft.alpha = fg.red, fg.green, fg.blue, 0.75
        for t in (self.t_markup, self.t_fence, self.t_done):
            t.set_property("foreground-rgba", dim)
        for t in self.t_quote:
            t.set_property("foreground-rgba", soft)
        faint = Gdk.RGBA()
        faint.red, faint.green, faint.blue, faint.alpha = fg.red, fg.green, fg.blue, 0.09
        self.t_inline["code"].set_property("background-rgba", faint)
        dark = Adw.StyleManager.get_default().get_dark()
        self.t_inline["highlight"].set_property(
            "background-rgba",
            _rgba("rgba(255, 200, 0, 0.30)" if dark else "rgba(255, 214, 0, 0.45)"),
        )
        accent = Adw.StyleManager.get_default().get_accent_color_rgba()
        self.t_inline["link"].set_property("foreground-rgba", accent)
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    # --- layout ------------------------------------------------------------------------

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        margin = max(MIN_MARGIN, (width - MAX_TEXT_WIDTH) // 2)
        column = width - 2 * margin
        if (margin, column) != (self._margin, self._column) and not self._pending_margin:
            # Changing margins inside an allocation would re-enter it: do it next frame.
            def apply() -> bool:
                self._pending_margin = 0
                self._margin, self._column = margin, column
                self.set_left_margin(margin)
                self.set_right_margin(margin)
                self._apply_margins()
                for n, info in enumerate(self._infos):  # pictures fit the new column
                    if info.kind == "image":
                        self._style_line(n)
                return GLib.SOURCE_REMOVE

            self._pending_margin = GLib.idle_add(apply)
        Gtk.TextView.do_size_allocate(self, width, height, baseline)

    # --- loading and text --------------------------------------------------------------

    def load_text(self, text: str) -> None:
        """Show a note's text as it is (no renumbering, no rewrites), not undoable."""
        self._loading = True
        self._lines, self._infos, self._blocks = [], [], []
        self.buffer.begin_irreversible_action()
        self.buffer.set_text(text)
        self.buffer.end_irreversible_action()
        self._loading = False

    def text(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, True)

    def line_text(self, line: int) -> str:
        start = self._line_start(line)
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        return self.buffer.get_text(start, end, True)

    def headings(self) -> list[md.Heading]:
        return md.outline(self._lines, self._infos)

    def cursor_line(self) -> int:
        return self._cursor().get_line()

    def go_to_line(self, line: int) -> None:
        """Put the cursor at the start of a line's text and scroll that line to the top."""
        if not 0 <= line < len(self._infos):
            return
        column = min(self._infos[line].content, len(self._lines[line]))
        self.buffer.place_cursor(self.buffer.get_iter_at_line_offset(line, column)[1])
        self.scroll_to_mark(self.buffer.get_insert(), 0.0, True, 0.0, 0.0)
        self.grab_focus()

    def _line_start(self, line: int) -> Gtk.TextIter:
        return self.buffer.get_iter_at_line(line)[1]

    def _cursor(self) -> Gtk.TextIter:
        return self.buffer.get_iter_at_mark(self.buffer.get_insert())

    # --- restyling ---------------------------------------------------------------------

    def _set_undoing(self, _buffer, value: bool) -> None:
        self._undoing = value

    def _on_changed(self, _buffer: Gtk.TextBuffer) -> None:
        if self._editing:
            return
        changed = self._restyle()
        if changed is not None and not self._loading and not self._undoing:
            a, z = changed
            if self._touched is not None:
                a, z = min(a, self._touched[0]), max(z, self._touched[1])
            self._touched = (a, z)

    def _on_user_action_done(self, _buffer: Gtk.TextBuffer) -> None:
        touched, self._touched = self._touched, None
        if self._editing or self._undoing or touched is None:
            return
        self._editing = True
        try:
            self._apply_shorthand()
        finally:
            self._editing = False
        self._restyle()
        self._renumber(*touched)

    def _restyle(self) -> tuple[int, int] | None:
        """Re-tag the lines whose text or kind changed. Returns the lines touched (for
        renumbering), or None if nothing changed."""
        lines = self.text().split("\n")
        infos = md.classify(lines)
        old_lines, old_infos = self._lines, self._infos
        start = 0
        limit = min(len(lines), len(old_lines))
        # Line infos are shared (cached) objects: "is" settles almost every line.
        while (
            start < limit
            and lines[start] == old_lines[start]
            and (infos[start] is old_infos[start] or infos[start] == old_infos[start])
        ):
            start += 1
        end_new, end_old = len(lines) - 1, len(old_lines) - 1
        while (
            end_new >= start
            and end_old >= start
            and lines[end_new] == old_lines[end_old]
            and (infos[end_new] is old_infos[end_old] or infos[end_new] == old_infos[end_old])
        ):
            end_new -= 1
            end_old -= 1
        self._lines, self._infos = lines, infos
        self._blocks = md.code_blocks(infos)
        self._cursor_line = self._cursor().get_line()
        if end_new >= start:
            for line in range(start, end_new + 1):
                self._style_line(line)
        self._sync_cursor_markup()
        self.queue_draw()
        if end_new >= start:
            return start, end_new
        if len(lines) != len(old_lines):  # lines were only removed: the join point
            point = min(start, len(lines) - 1)
            return point, point
        return None

    def _style_line(self, line: int) -> None:
        if line >= len(self._infos):
            return
        b = self.buffer
        start = self._line_start(line)
        end = start.copy()
        if not end.forward_line():  # the newline too: empty lines keep paragraph tags
            end = b.get_end_iter()
        for t in self._tags:
            b.remove_tag(t, start, end)
        info = self._infos[line]
        kind = info.kind

        def span(tag: Gtk.TextTag, a: int, z: int | None = None) -> None:
            s = start.copy()
            s.set_line_offset(min(a, s.get_chars_in_line()))
            e = end if z is None else start.copy()
            if z is not None:
                e.set_line_offset(min(z, e.get_chars_in_line()))
            b.apply_tag(tag, s, e)

        on_cursor = line == self._cursor_line
        if kind == "heading":
            span(self.t_heading[min(info.level, 3)], 0)
            span(self.t_markup if on_cursor else self.t_hidden, 0, info.marker)
        elif kind in ("bullet", "task", "ordered"):
            span(self.t_list[info.depth], 0)
            if info.hidden:
                span(self.t_hidden, 0, info.hidden)
            if kind == "ordered":
                shown = line == self._number_line
                span(self.t_markup if shown else self.t_hidden, info.hidden, info.marker)
            if kind == "task" and info.checked:
                span(self.t_done, info.content, len(self._lines[line]))
        elif kind == "quote":
            span(self.t_quote[min(info.depth, len(self.t_quote)) - 1], 0)
            span(self.t_hidden, 0, info.hidden)
        elif kind == "rule":
            span(self.t_markup if on_cursor else self.t_hidden, 0, info.marker)
        elif kind == "fence":
            span(self.t_fence, 0)
        elif kind == "code":
            span(self.t_code, 0)
        elif kind == "image":
            texture = self._texture(info.url)
            if texture is not None:
                _w, h = self._image_size(texture)
                span(self._image_tag(h), 0)
            if texture is None or on_cursor:
                span(self.t_markup, 0, info.marker)  # the link itself, to edit it
            else:
                span(self.t_hidden, 0, info.marker)
                span(self.t_image_row, 0)
        if kind not in ("code", "fence", "rule", "image", "blank"):
            marker = self.t_markup if on_cursor else self.t_hidden
            for sp in md.inline_spans(self._lines[line], info.content):
                span(self.t_inline[sp.kind], sp.inner_start, sp.inner_end)
                if sp.start < sp.inner_start:
                    span(marker, sp.start, sp.inner_start)
                if sp.inner_end < sp.end:
                    span(marker, sp.inner_end, sp.end)

    def _on_cursor_moved(self, *_args) -> None:
        # Show the new line's markup a moment later, not while GTK is still handling the
        # click (or key) that moved the cursor: re-laying out the line under the pointer
        # mid-click would move the cursor off the character that was clicked.
        if not self._cursor_idle:
            self._cursor_idle = GLib.idle_add(self._after_cursor_moved)

    def _after_cursor_moved(self) -> bool:
        self._cursor_idle = 0
        if self.buffer.get_line_count() == len(self._infos):  # else a restyle is due
            self._sync_cursor_markup()
            self._sync_number()
            self._keep_cursor_out_of_markers(self._cursor())
        return GLib.SOURCE_REMOVE

    def _sync_cursor_markup(self) -> None:
        """Show markup (headings, rules, inline styles, image links) on the cursor's
        line only."""
        line = self._cursor().get_line()
        shown = self.buffer.get_iter_at_mark(self._shown).get_line()
        self._cursor_line = line
        if shown == line:
            return
        for n in (shown, line):
            if 0 <= n < len(self._infos) and self._infos[n].kind not in ("code", "fence"):
                self._style_line(n)
        self.buffer.move_mark(self._shown, self._line_start(line))

    def _sync_number(self) -> None:
        """Close the number being edited once the cursor has left it. Checked once an
        edit is over (on idle), not mid-edit: changing "3." to "3)" passes through "3 "."""
        line = self._number_line
        if line is None:
            return
        cursor = self._cursor()
        info = self._infos[line] if line < len(self._infos) else None
        if (
            info is not None
            and info.kind == "ordered"
            and cursor.get_line() == line
            and info.hidden <= cursor.get_line_offset() < info.content
        ):
            return
        self._number_line = None
        if info is not None:
            self._style_line(line)
        self.queue_draw()

    def _open_number(self, line: int, column: int) -> None:
        """Show a numbered item's "1. " so it can be edited, with the cursor at column."""
        self._number_line = line
        self._style_line(line)
        it = self._line_start(line)
        it.set_line_offset(column)
        self.buffer.place_cursor(it)
        self.queue_draw()

    def _keep_cursor_out_of_markers(self, cursor: Gtk.TextIter) -> None:
        """A cursor inside a hidden marker (Home, arrow keys, a click) moves to the text;
        inside a number being edited it may stay (but not in the indent before it)."""
        if self._editing or self.buffer.get_has_selection():
            return
        line = cursor.get_line()
        if line >= len(self._infos):
            return
        info = self._infos[line]
        col = cursor.get_line_offset()
        if info.kind == "ordered" and line == self._number_line:
            if col >= info.hidden:
                return
            target = info.hidden
        elif info.kind == "ordered":
            if col >= info.content:
                return
            target = info.content
        elif not info.hidden or col >= info.hidden:
            return
        else:
            target = info.content
        it = self._line_start(line)
        it.set_line_offset(min(target, it.get_chars_in_line()))
        self._editing = True
        try:
            self.buffer.place_cursor(it)
        finally:
            self._editing = False

    # --- automatic edits ----------------------------------------------------------------

    def _replace(self, line: int, start_col: int, end_col: int, text: str) -> None:
        s = self._line_start(line)
        s.set_line_offset(start_col)
        e = self._line_start(line)
        e.set_line_offset(end_col)
        self.buffer.delete(s, e)
        self.buffer.insert(s, text)

    def _set_line(self, line: int, text: str, cursor_col: int | None = None) -> None:
        old = self.line_text(line)
        self._replace(line, 0, len(old), text)
        if cursor_col is not None:
            it = self._line_start(line)
            it.set_line_offset(min(cursor_col, len(text)))
            self.buffer.place_cursor(it)

    def _apply_shorthand(self) -> None:
        """ "[] " just typed at a line start becomes "- [ ] "; "- " typed on an empty
        numbered item makes it a bullet, and "1. " (or "a) " / "i. " where a sub-list goes)
        the other way round. Nothing is rewritten inside code blocks."""
        cursor = self._cursor()
        line, col = cursor.get_line(), cursor.get_line_offset()
        if line >= len(self._infos) or len(self._lines) != len(self._infos):
            return
        found = md.shorthand(self._lines, self._infos, line)
        if found is None or col != found[0]:
            return
        length, replacement = found
        self._replace(line, 0, length, replacement)

    def _renumber(self, first: int, last: int) -> None:
        """Renumber the ordered list(s) around the lines just changed."""
        infos, lines = self._infos, self._lines
        if not any(i.kind == "ordered" for i in infos):
            return
        a, z = first, min(last, len(infos) - 1)
        while a > 0 and infos[a - 1].kind in (*md.LIST_KINDS, "blank"):
            a -= 1
        while z + 1 < len(infos) and infos[z + 1].kind in (*md.LIST_KINDS, "blank"):
            z += 1
        edits = [e for e in md.renumber(lines, infos) if a <= e[0] <= z]
        if not edits:
            return
        cursor = self.buffer.create_mark(None, self._cursor(), False)
        self._editing = True
        try:
            for line, start, end, number in edits:
                self._replace(line, start, end, number)
        finally:
            self._editing = False
        self.buffer.place_cursor(self.buffer.get_iter_at_mark(cursor))
        self.buffer.delete_mark(cursor)
        self._restyle()

    def _user_edit(self, edit) -> None:
        """Run an edit as one undo step (renumbering follows at the end of it)."""
        self.buffer.begin_user_action()
        self._editing = True
        try:
            edit()
        finally:
            self._editing = False
        changed = self._restyle()
        if changed is not None:
            a, z = changed
            if self._touched is not None:
                a, z = min(a, self._touched[0]), max(z, self._touched[1])
            self._touched = (a, z)
        self.buffer.end_user_action()  # -> _on_user_action_done: renumber
        self.scroll_mark_onscreen(self.buffer.get_insert())

    # --- keys ------------------------------------------------------------------------------

    def _on_key(self, _ctrl, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if self._preedit:
            return False  # the input method is composing: Enter, Tab... are its keys
        mods = state & Gtk.accelerator_get_default_mod_mask()
        shift = mods == Gdk.ModifierType.SHIFT_MASK
        ctrl = mods == Gdk.ModifierType.CONTROL_MASK
        if keyval in _ENTER and ctrl:
            return self.toggle_task(self._cursor().get_line())
        ctrl_shift = mods == Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        if ctrl or ctrl_shift:
            style = SHORTCUTS.get((Gdk.keyval_to_lower(keyval), ctrl_shift))
            if style is not None:
                self.wrap_selection(style)
                return True
            if ctrl and Gdk.keyval_to_lower(keyval) == Gdk.KEY_k:
                self.insert_link()
                return True
        if keyval in _ENTER and not mods:
            return self._enter()
        if keyval == Gdk.KEY_Tab and not mods:
            return self._indent_lines(md.indent)
        if keyval == Gdk.KEY_ISO_Left_Tab or (keyval == Gdk.KEY_Tab and shift):
            self._indent_lines(md.outdent)
            return True  # never move focus out of the editor
        if keyval == Gdk.KEY_BackSpace and not mods:
            return self._backspace()
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left) and not mods:
            return self._left_over_marker()
        return False

    def _current(self) -> tuple[int, int, str, md.LineInfo] | None:
        cursor = self._cursor()
        line = cursor.get_line()
        if line >= len(self._infos):
            return None
        return line, cursor.get_line_offset(), self.line_text(line), self._infos[line]

    def _enter(self) -> bool:
        if self.buffer.get_has_selection() or (here := self._current()) is None:
            return False
        line, col, text, info = here
        if info.kind == "ordered" and line == self._number_line:
            self._open_number_done(line, info)  # Enter in the number: back to the text
            return True
        if info.kind in (*md.LIST_KINDS, "quote") and col >= info.content:
            if md.is_empty_item(text, info):
                # Enter on an empty item: step out one level (as an item of the list it
                # steps back into: a bullet under "1." becomes "2."), or end the list.
                if info.kind in md.LIST_KINDS and info.indent:
                    new = md.step_out(self._lines, self._infos, line)
                    self._user_edit(lambda: self._set_line(line, new, len(new)))
                else:
                    self._user_edit(lambda: self._set_line(line, "", 0))
                return True
            prefix = md.continuation(text, info)
            self._user_edit(lambda: self.buffer.insert_at_cursor("\n" + prefix))
            return True
        if info.kind == "fence" and col == len(text) and self._fence_is_open(line):
            # "```" + Enter: close the block and put the cursor inside it.
            def close() -> None:
                self.buffer.insert_at_cursor("\n\n" + text.strip()[:3])
                it = self._line_start(line + 1)
                self.buffer.place_cursor(it)

            self._user_edit(close)
            return True
        return False

    def _fence_is_open(self, line: int) -> bool:
        """True if the fence on this line opens a block nothing closes."""
        rest = self._infos[line + 1 :]
        opening = sum(1 for i in self._infos[:line] if i.kind == "fence") % 2 == 0
        return opening and all(i.kind == "code" for i in rest)

    def _selected_lines(self) -> range:
        start, end = self.buffer.get_selection_bounds() or (self._cursor(), self._cursor())
        last = end.get_line()
        if end.starts_line() and end.get_line() > start.get_line():
            last -= 1  # a selection ending at a line start doesn't include that line
        return range(start.get_line(), last + 1)

    def _indent_lines(self, change) -> bool:
        lines = [n for n in self._selected_lines() if self._infos[n].kind in md.LIST_KINDS]
        if not lines:
            return False  # Tab elsewhere inserts a tab as usual

        def edit() -> None:
            text = list(self._lines)  # as edited so far: each line's new level depends on it
            for n in lines:
                old = text[n]
                new = text[n] = md.reindent(text, n, change)  # a numbered item's marker too
                if new != old:
                    cursor = self._cursor()
                    col = cursor.get_line_offset() if cursor.get_line() == n else None
                    self._set_line(n, new, None if col is None else col + len(new) - len(old))

        self._user_edit(edit)
        return True

    def _open_number_done(self, line: int, info: md.LineInfo) -> None:
        it = self._line_start(line)
        it.set_line_offset(min(info.content, it.get_chars_in_line()))
        self.buffer.place_cursor(it)  # _sync_number closes it

    def _left_over_marker(self) -> bool:
        """Left at the start of an item's text goes to the previous line, not into the
        hidden marker; on a numbered item it steps into the number, to edit it."""
        if self.buffer.get_has_selection() or (here := self._current()) is None:
            return False
        line, col, text, info = here
        if info.kind == "ordered" and line != self._number_line and col == info.content:
            self._open_number(line, len(text[: info.content].rstrip()))  # after the "."
            return True
        if info.kind == "ordered":
            if line != self._number_line or col != info.hidden:
                return False
        elif not info.hidden or col != info.content:
            return False
        it = self._line_start(line)
        if line > 0:
            it.backward_char()  # the end of the previous line
        self.buffer.place_cursor(it)
        self.scroll_mark_onscreen(self.buffer.get_insert())
        return True

    def _backspace(self) -> bool:
        """At the start of an item's or heading's text: outdent, or drop the marker."""
        if self.buffer.get_has_selection() or (here := self._current()) is None:
            return False
        line, col, text, info = here
        if info.kind not in (*md.LIST_KINDS, "quote", "heading") or col != info.content:
            return False
        if info.kind in md.LIST_KINDS and info.indent:
            new = md.reindent(self._lines, line, md.outdent)
            self._user_edit(lambda: self._set_line(line, new, col - (len(text) - len(new))))
        else:
            new = md.without_marker(text, info)
            self._user_edit(lambda: self._set_line(line, new, 0))
        return True

    def _select(self, line: int, a: int, z: int) -> None:
        start = self._line_start(line)
        start.set_line_offset(a)
        end = self._line_start(line)
        end.set_line_offset(z)
        self.buffer.select_range(start, end)

    def wrap_selection(self, style: str) -> None:
        """Ctrl+B and friends: wrap the selection in a style (or unwrap it)."""
        bounds = self.buffer.get_selection_bounds()
        a, z = bounds if bounds else (self._cursor(), self._cursor())
        if a.get_line() != z.get_line():
            return  # styles don't span lines in markdown
        line, text = a.get_line(), self.line_text(a.get_line())
        new, s, e = md.toggle_wrap(text, a.get_line_offset(), z.get_line_offset(), style)

        def edit() -> None:
            self._set_line(line, new)
            self._select(line, s, e)

        self._user_edit(edit)

    def insert_link(self) -> None:
        """Ctrl+K: [selection](|), or [|](url) when a URL is selected."""
        bounds = self.buffer.get_selection_bounds()
        a, z = bounds if bounds else (self._cursor(), self._cursor())
        if a.get_line() != z.get_line():
            return
        line, text = a.get_line(), self.line_text(a.get_line())
        i, j = a.get_line_offset(), z.get_line_offset()
        chosen = text[i:j]
        if chosen.startswith(("http://", "https://", "www.")):
            new, cursor = f"{text[:i]}[]({chosen}){text[j:]}", i + 1
        else:
            new, cursor = f"{text[:i]}[{chosen}](){text[j:]}", i + len(chosen) + 3

        def edit() -> None:
            self._set_line(line, new)
            self._select(line, cursor, cursor)

        self._user_edit(edit)

    def toggle_task(self, line: int) -> bool:
        if not 0 <= line < len(self._infos) or self._infos[line].kind != "task":
            return False
        text = self.line_text(line)
        new = md.toggle_task(text, self._infos[line])
        cursor = self.buffer.create_mark(None, self._cursor(), False)

        def edit() -> None:
            self._set_line(line, new)
            self.buffer.place_cursor(self.buffer.get_iter_at_mark(cursor))

        self._user_edit(edit)
        self.buffer.delete_mark(cursor)
        return True

    # --- checkbox clicks ---------------------------------------------------------------

    def _on_click(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        bx, by = self.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        state = gesture.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK and (url := self.link_at(bx, by)):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            if self.link_handler is not None:
                self.link_handler(url)
            return
        for line, box in self._number_boxes.items():
            if box.contains_point(Graphene.Point().init(bx, by)):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self._open_number(line, self._infos[line].hidden)
                return
        for line, box in self._checkboxes.items():
            pad = 4  # a slightly bigger target than the drawn box
            if box.get_x() - pad <= bx <= box.get_x() + box.get_width() + pad and (
                box.get_y() - pad <= by <= box.get_y() + box.get_height() + pad
            ):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.toggle_task(line)
                return

    def link_at(self, bx: int, by: int) -> str | None:
        """The URL of the link (or picture) at a point in buffer coordinates."""
        ok, it = self.get_iter_at_location(bx, by)
        if not ok:
            return None
        line, col = it.get_line(), it.get_line_offset()
        if line >= len(self._infos):
            return None
        info = self._infos[line]
        if info.kind == "image":
            return info.url
        if info.kind in ("code", "fence"):
            return None
        for sp in md.inline_spans(self._lines[line], info.content):
            if sp.kind in LINK_KINDS and sp.start <= col < sp.end:
                return sp.url
        return None

    def _on_query_tooltip(self, _view, x: int, y: int, keyboard: bool, tooltip) -> bool:
        if keyboard:
            return False
        bx, by = self.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, x, y)
        url = self.link_at(bx, by)
        if url is None:
            return False
        tooltip.set_text(f"{url}\nCtrl+click to open")
        return True

    # --- pictures ------------------------------------------------------------------------

    def image_path(self, url: str) -> Path | None:
        """A picture's file, for a path relative to the note (not for web images)."""
        if self.base_dir is None or "://" in url or url.startswith(("www.", "mailto:")):
            return None
        path = Path(unquote(url))
        return path if path.is_absolute() else self.base_dir / path

    def _texture(self, url: str) -> Gdk.Texture | None:
        path = self.image_path(url)
        if path is None:
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = self._textures.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            texture = Gdk.Texture.new_from_filename(str(path))
        except GLib.Error as e:
            log.warning("cannot show %s: %s", path, e.message)
            texture = None
        self._textures[path] = (mtime, texture)
        return texture

    def _image_size(self, texture: Gdk.Texture) -> tuple[int, int]:
        """Shown size: the natural size, scaled down to fit the column and height cap."""
        w, h = texture.get_width(), texture.get_height()
        scale = min(1.0, max(self._column, 100) / w, MAX_IMAGE_HEIGHT / h)
        return max(1, round(w * scale)), max(1, round(h * scale))

    def _image_tag(self, height: int) -> Gtk.TextTag:
        """A tag that leaves room for a picture of this height below its line."""
        tag = self._image_tags.get(height)
        if tag is None:
            tag = self.buffer.create_tag(None, pixels_below_lines=height + sum(IMAGE_GAP))
            self._image_tags[height] = tag
            self._tags.append(tag)
        return tag

    def _on_paste(self, _view: Gtk.TextView) -> None:
        """Ctrl+V of a picture (a screenshot): save it next to the note and link it.
        Anything with text in it pastes as usual."""
        clipboard = self.get_clipboard()
        formats = clipboard.get_formats()
        has_text = formats.contain_mime_type("text/plain") or formats.contain_mime_type(
            "text/plain;charset=utf-8"
        )
        if has_text or not _offers_picture(formats):
            return
        self.stop_emission_by_name("paste-clipboard")
        clipboard.read_texture_async(None, self._on_pasted_texture)

    def _on_pasted_texture(self, clipboard: Gdk.Clipboard, result) -> None:
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error as e:
            self._error(f"Could not paste the picture: {e.message}")
            return
        if texture is None:
            return
        try:
            link = self.save_picture(texture)
        except OSError as e:
            self._error(f"Could not save the picture: {e}")
            return
        self.insert_picture_link(link)

    def save_picture(self, texture: Gdk.Texture) -> str:
        """Save a picture to attachments/ next to the note; returns its link path."""
        if self.base_dir is None:
            raise OSError("no note is open")
        directory = self.base_dir / md.ATTACHMENTS
        directory.mkdir(parents=True, exist_ok=True)
        taken = set(os.listdir(directory))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = md.attachment_name(self.note_stem, stamp, taken)
        if not texture.save_to_png(str(directory / name)):
            raise OSError(f"writing {name} failed")
        return f"{md.ATTACHMENTS}/{name}"

    def insert_picture_link(self, link: str) -> None:
        """Put ![](link) on a line of its own at the cursor; the cursor goes below it."""
        cursor = self._cursor()
        before = "" if cursor.starts_line() else "\n"
        self._user_edit(lambda: self.buffer.insert_at_cursor(f"{before}![]({link})\n"))

    def _error(self, message: str) -> None:
        log.error("%s", message)
        if self.error_handler is not None:
            self.error_handler(message)

    # --- drawing -------------------------------------------------------------------------

    def do_snapshot_layer(self, layer: Gtk.TextViewLayer, snapshot: Gtk.Snapshot) -> None:
        if layer != Gtk.TextViewLayer.BELOW_TEXT or not self._infos:
            return
        visible = self.get_visible_rect()
        top, bottom = visible.y, visible.y + visible.height
        fg = self.get_color()
        dim = Gdk.RGBA()
        dim.red, dim.green, dim.blue, dim.alpha = fg.red, fg.green, fg.blue, 0.5
        faint = Gdk.RGBA()
        faint.red, faint.green, faint.blue, faint.alpha = fg.red, fg.green, fg.blue, 0.08
        accent = Adw.StyleManager.get_default().get_accent_color_rgba()
        width = self.get_width()
        self._checkboxes = {}
        self._bullets = {}
        self._numbers = {}
        self._number_boxes = {}

        self._draw_code_blocks(snapshot, top, bottom, width, faint)
        it, _ = self.get_line_at_y(top)
        while True:
            line = it.get_line()
            y, height = self.get_line_yrange(it)
            if y > bottom or line >= len(self._infos):
                break
            info = self._infos[line]
            if info.kind == "bullet":
                glyph = BULLETS[info.depth % len(BULLETS)]
                baseline = y + self._baseline(line, info)
                self._bullets[line] = baseline
                self._glyph_on_baseline(snapshot, glyph, self._slot_x(info.depth), baseline, fg)
            elif info.kind == "ordered" and line != self._number_line:  # else "1. " shows
                label = md.marker_text(self._lines[line], info)  # as written: 1. / b) / iv.
                baseline = y + self._baseline(line, info)
                right = self._margin + (info.depth + 1) * LIST_STEP - NUMBER_GAP
                self._numbers[line] = (label, right, baseline)
                self._number_boxes[line] = self._label_on_baseline(
                    snapshot, label, right, baseline, fg
                )
            elif info.kind == "task":
                self._checkbox(snapshot, line, info, y, dim, accent)
            elif info.kind == "quote":
                for d in range(info.depth):
                    x = self._margin + d * QUOTE_STEP + 2
                    snapshot.append_color(accent if d == 0 else dim, _rect(x, y, 3, height))
            elif info.kind == "image" and (texture := self._texture(info.url)) is not None:
                w, h = self._image_size(texture)
                box = _rect(self._margin, y + height - h - IMAGE_GAP[1], w, h)
                rounded = Gsk.RoundedRect()
                rounded.init_from_rect(box, 6)
                snapshot.push_rounded_clip(rounded)
                snapshot.append_texture(texture, box)
                snapshot.pop()
            elif info.kind == "rule" and line != self._cursor_line:
                mid = y + (height - self.get_pixels_below_lines()) / 2
                snapshot.append_color(dim, _rect(self._margin, mid, width - 2 * self._margin, 1))
            if not it.forward_line():
                break

    def _baseline(self, line: int, info: md.LineInfo) -> float:
        """Distance from a line's top to the baseline of its first row of text.

        Measured on a layout of the line's own first characters, so a row that is
        taller because of CJK text gets its real baseline. (The text view's own
        character locations can't be used: right after hidden markup, and on an empty
        item, they report a height of 0.)"""
        text = self._lines[line][info.content : info.content + 40]
        layout = self.create_pango_layout("Ag" + text)
        return layout.get_baseline() / Pango.SCALE

    def _cap_height(self) -> float:
        """Height of a capital letter above the baseline, in the editor's font."""
        layout = self.create_pango_layout("H")
        ink, _logical = layout.get_pixel_extents()
        return layout.get_baseline() / Pango.SCALE - ink.y

    def _slot_x(self, depth: int) -> float:
        """Centre of the space before a list item's text."""
        return self._margin + depth * LIST_STEP + LIST_STEP * 0.5

    def _glyph_on_baseline(self, snapshot, text: str, cx: float, baseline: float, color) -> None:
        """Draw text centred on cx, sitting on the given baseline like typed text."""
        layout = self.create_pango_layout(text)
        _ink, logical = layout.get_pixel_extents()
        top = baseline - layout.get_baseline() / Pango.SCALE
        snapshot.save()
        snapshot.translate(Graphene.Point().init(cx - logical.width / 2, top))
        snapshot.append_layout(layout, color)
        snapshot.restore()

    def _label_on_baseline(
        self, snapshot, text: str, right: float, baseline: float, color
    ) -> Graphene.Rect:
        """A list number ending at `right`, on the text's baseline (long ones like "xviii."
        reach into the margin, as in Notion). Returns where it was drawn."""
        layout = self.create_pango_layout(text)
        _ink, logical = layout.get_pixel_extents()
        top = baseline - layout.get_baseline() / Pango.SCALE
        snapshot.save()
        snapshot.translate(Graphene.Point().init(right - logical.width, top))
        snapshot.append_layout(layout, color)
        snapshot.restore()
        return _rect(right - logical.width, top, logical.width, logical.height)

    def _glyph(self, snapshot, text: str, cx: float, row: tuple[int, int], color) -> None:
        layout = self.create_pango_layout(text)
        _ink, logical = layout.get_pixel_extents()
        snapshot.save()
        snapshot.translate(
            Graphene.Point().init(cx - logical.width / 2, row[0] + (row[1] - logical.height) / 2)
        )
        snapshot.append_layout(layout, color)
        snapshot.restore()

    def _checkbox(self, snapshot, line, info, top, border, accent) -> None:
        # Centred between the baseline and the top of capitals, like the text beside it.
        middle = top + self._baseline(line, info) - self._cap_height() / 2
        x = self._slot_x(info.depth) - CHECKBOX / 2
        y = round(middle - CHECKBOX / 2)
        box = _rect(x, y, CHECKBOX, CHECKBOX)
        self._checkboxes[line] = box
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(box, 4)
        if info.checked:
            snapshot.push_rounded_clip(rounded)
            snapshot.append_color(accent, box)
            snapshot.pop()
            white = _rgba("white")
            self._glyph(snapshot, "✓", x + CHECKBOX / 2, (int(y) - 1, CHECKBOX + 2), white)
        else:
            width = [1.5] * 4
            snapshot.append_border(rounded, width, [border] * 4)

    def _draw_code_blocks(self, snapshot, top, bottom, width, color) -> None:
        """A rounded background behind each fenced block that is on screen (only those
        are measured: finding a line's position can lay out the text above it)."""
        first_shown = self.get_line_at_y(top)[0].get_line()
        last_shown = self.get_line_at_y(bottom)[0].get_line()
        for n, last in self._blocks:
            if last < first_shown:
                continue
            if n > last_shown:
                break
            y1, _ = self.get_line_yrange(self._line_start(n))
            y2, h2 = self.get_line_yrange(self._line_start(last))
            if y1 <= bottom and y2 + h2 >= top:
                box = _rect(self._margin, y1, width - 2 * self._margin, y2 + h2 - y1)
                rounded = Gsk.RoundedRect()
                rounded.init_from_rect(box, 8)
                snapshot.push_rounded_clip(rounded)
                snapshot.append_color(color, box)
                snapshot.pop()
