"""The Notes editor: a Gtk.TextView that formats markdown blocks as you type.

The buffer always holds the plain markdown. After every change each line is classified
(notes_markdown.classify) and only lines whose kind changed are re-tagged:

- headings get a bigger font; their "## " is dimmed on the cursor's line, hidden elsewhere
- list, checkbox and quote markers are always hidden; a bullet, checkbox or bar is drawn
  in their place (snapshot_layer), and the lines are indented with tag margins
- "---" is drawn as a line (its text shows on the cursor's line); code blocks get a
  monospace font on a rounded background

Typing rules live in on_key: Enter continues a list (and ends it on an empty item), Tab /
Shift+Tab indent list items, Backspace at the start of an item or heading removes its
marker, Ctrl+Enter or a click toggles a checkbox. "[] " becomes a checkbox, and ordered
lists renumber themselves. Those edits happen inside the same user action as the
keystroke, so one Ctrl+Z undoes both.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .. import notes_markdown as md  # noqa: E402

log = logging.getLogger(__name__)

MAX_TEXT_WIDTH = 820  # the text column; wider windows get side margins
MIN_MARGIN = 32
LIST_STEP = 28  # px per list level; the bullet or checkbox sits in the step before the text
QUOTE_STEP = 18
CODE_INSET = 14
CHECKBOX = 15
BULLETS = ("•", "◦", "▪")
HEADING_SCALES = {1: 1.6, 2: 1.35, 3: 1.15}
_ENTER = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter)


def _rect(x: float, y: float, w: float, h: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, w, h)


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
        style = Adw.StyleManager.get_default()
        # Colours follow the theme; the new CSS applies a moment after the switch.
        style.connect("notify::dark", lambda *_a: GLib.idle_add(self._update_colors))
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
        self.t_hidden = tag("md-hidden", invisible=True)
        self.t_markup = tag("md-markup")  # dimmed markup; colour set in _update_colors
        self.t_list = [tag(f"md-list{d}") for d in range(md.MAX_DEPTH + 1)]
        self.t_quote = [tag(f"md-quote{d}", style=Pango.Style.ITALIC) for d in range(1, 7)]
        self.t_code = tag("md-code", family="monospace", scale=0.92)
        self.t_fence = tag("md-fence", family="monospace", scale=0.85)
        self.t_done = tag("md-done", strikethrough=True)
        self.t_number = tag("md-number", weight=Pango.Weight.BOLD)
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
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    # --- layout ------------------------------------------------------------------------

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        margin = max(MIN_MARGIN, (width - MAX_TEXT_WIDTH) // 2)
        if margin != self._margin and not self._pending_margin:
            # Changing margins inside an allocation would re-enter it: do it next frame.
            def apply() -> bool:
                self._pending_margin = 0
                self._margin = margin
                self.set_left_margin(margin)
                self.set_right_margin(margin)
                self._apply_margins()
                return GLib.SOURCE_REMOVE

            self._pending_margin = GLib.idle_add(apply)
        Gtk.TextView.do_size_allocate(self, width, height, baseline)

    # --- loading and text --------------------------------------------------------------

    def load_text(self, text: str) -> None:
        """Show a note's text as it is (no renumbering, no rewrites), not undoable."""
        self._loading = True
        self._lines, self._infos = [], []
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
        while (
            start < limit and lines[start] == old_lines[start] and infos[start] == old_infos[start]
        ):
            start += 1
        end_new, end_old = len(lines) - 1, len(old_lines) - 1
        while (
            end_new >= start
            and end_old >= start
            and lines[end_new] == old_lines[end_old]
            and infos[end_new] == old_infos[end_old]
        ):
            end_new -= 1
            end_old -= 1
        self._lines, self._infos = lines, infos
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
                span(self.t_number, info.hidden, info.marker - 1)
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

    def _on_cursor_moved(self, *_args) -> None:
        if self.buffer.get_line_count() != len(self._infos):
            return  # mid-edit: the "changed" handler restyles and syncs
        self._sync_cursor_markup()
        self._keep_cursor_out_of_markers(self._cursor())

    def _sync_cursor_markup(self) -> None:
        """Show heading/rule markup on the cursor's line only."""
        line = self._cursor().get_line()
        shown = self.buffer.get_iter_at_mark(self._shown).get_line()
        self._cursor_line = line
        if shown == line:
            return
        for n in (shown, line):
            if 0 <= n < len(self._infos) and self._infos[n].kind in ("heading", "rule"):
                self._style_line(n)
        self.buffer.move_mark(self._shown, self._line_start(line))

    def _keep_cursor_out_of_markers(self, cursor: Gtk.TextIter) -> None:
        """A cursor inside a hidden marker (Home, arrow keys, a click) moves to the text."""
        if self._editing or self.buffer.get_has_selection():
            return
        line = cursor.get_line()
        if line >= len(self._infos):
            return
        info = self._infos[line]
        if not info.hidden or cursor.get_line_offset() >= info.hidden:
            return
        target = info.hidden if info.kind == "ordered" else info.content
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
        """ "[] " just typed at a line start becomes "- [ ] "."""
        cursor = self._cursor()
        line, col = cursor.get_line(), cursor.get_line_offset()
        found = md.task_shorthand(self.line_text(line))
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
        if keyval in _ENTER and not mods:
            return self._enter()
        if keyval == Gdk.KEY_Tab and not mods:
            return self._indent_lines(md.indent)
        if keyval == Gdk.KEY_ISO_Left_Tab or (keyval == Gdk.KEY_Tab and shift):
            self._indent_lines(md.outdent)
            return True  # never move focus out of the editor
        if keyval == Gdk.KEY_BackSpace and not mods:
            return self._backspace()
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
        if info.kind in (*md.LIST_KINDS, "quote") and col >= info.content:
            if md.is_empty_item(text, info):
                # Enter on an empty item: step out one level, or end the list.
                if info.kind in md.LIST_KINDS and info.indent:
                    new = md.outdent(text)
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
            for n in lines:
                old = self.line_text(n)
                new = change(old)
                if new != old:
                    cursor = self._cursor()
                    col = cursor.get_line_offset() if cursor.get_line() == n else None
                    self._set_line(n, new, None if col is None else col + len(new) - len(old))

        self._user_edit(edit)
        return True

    def _backspace(self) -> bool:
        """At the start of an item's or heading's text: outdent, or drop the marker."""
        if self.buffer.get_has_selection() or (here := self._current()) is None:
            return False
        line, col, text, info = here
        if info.kind not in (*md.LIST_KINDS, "quote", "heading") or col != info.content:
            return False
        if info.kind in md.LIST_KINDS and info.indent:
            new = md.outdent(text)
            self._user_edit(lambda: self._set_line(line, new, col - (len(text) - len(new))))
        else:
            new = md.without_marker(text, info)
            self._user_edit(lambda: self._set_line(line, new, 0))
        return True

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
        for line, box in self._checkboxes.items():
            pad = 4  # a slightly bigger target than the drawn box
            if box.get_x() - pad <= bx <= box.get_x() + box.get_width() + pad and (
                box.get_y() - pad <= by <= box.get_y() + box.get_height() + pad
            ):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.toggle_task(line)
                return

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

        self._draw_code_blocks(snapshot, top, bottom, width, faint)
        it, _ = self.get_line_at_y(top)
        while True:
            line = it.get_line()
            y, height = self.get_line_yrange(it)
            if y > bottom or line >= len(self._infos):
                break
            info = self._infos[line]
            first = self._first_row(it, info)
            if info.kind == "bullet":
                glyph = BULLETS[info.depth % len(BULLETS)]
                self._glyph(snapshot, glyph, self._slot_x(info.depth), first, fg)
            elif info.kind == "task":
                self._checkbox(snapshot, line, info, first, dim, accent)
            elif info.kind == "quote":
                for d in range(info.depth):
                    x = self._margin + d * QUOTE_STEP + 2
                    snapshot.append_color(accent if d == 0 else dim, _rect(x, y, 3, height))
            elif info.kind == "rule" and line != self._cursor_line:
                mid = first[0] + first[1] / 2
                snapshot.append_color(dim, _rect(self._margin, mid, width - 2 * self._margin, 1))
            if not it.forward_line():
                break

    def _first_row(self, it: Gtk.TextIter, info: md.LineInfo) -> tuple[int, int]:
        """(y, height) of a line's first display row (it may wrap onto more)."""
        content = it.copy()
        content.set_line_offset(min(info.content, content.get_chars_in_line()))
        y, height = self.get_line_yrange(it)
        location = self.get_iter_location(content)
        return location.y, location.height or height

    def _slot_x(self, depth: int) -> float:
        """Centre of the space before a list item's text."""
        return self._margin + depth * LIST_STEP + LIST_STEP * 0.5

    def _glyph(self, snapshot, text: str, cx: float, row: tuple[int, int], color) -> None:
        layout = self.create_pango_layout(text)
        _ink, logical = layout.get_pixel_extents()
        snapshot.save()
        snapshot.translate(
            Graphene.Point().init(cx - logical.width / 2, row[0] + (row[1] - logical.height) / 2)
        )
        snapshot.append_layout(layout, color)
        snapshot.restore()

    def _checkbox(self, snapshot, line, info, row, border, accent) -> None:
        x = self._slot_x(info.depth) - CHECKBOX / 2
        y = row[0] + (row[1] - CHECKBOX) / 2
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
        """A rounded background behind each fenced block that is on screen."""
        infos = self._infos
        n = 0
        while n < len(infos):
            if infos[n].kind != "fence":
                n += 1
                continue
            end = n + 1
            while end < len(infos) and infos[end].kind == "code":
                end += 1
            last = min(end, len(infos) - 1)  # the closing fence, or the last line
            y1, _ = self.get_line_yrange(self._line_start(n))
            y2, h2 = self.get_line_yrange(self._line_start(last))
            if y1 <= bottom and y2 + h2 >= top:
                box = _rect(self._margin, y1, width - 2 * self._margin, y2 + h2 - y1)
                rounded = Gsk.RoundedRect()
                rounded.init_from_rect(box, 8)
                snapshot.push_rounded_clip(rounded)
                snapshot.append_color(color, box)
                snapshot.pop()
            n = end + 1
