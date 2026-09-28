"""The outline: the open note's headings in a popover. Picking one jumps to it."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..notes_markdown import Heading  # noqa: E402

INDENT_PX = 16  # per heading level below the first
MAX_HEIGHT = 520


class OutlineRow(Gtk.ListBoxRow):
    def __init__(self, heading: Heading) -> None:
        label = Gtk.Label(
            label=heading.text,
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=48,
            margin_start=INDENT_PX * (min(heading.level, 3) - 1),
        )
        if heading.level == 1:
            label.add_css_class("heading")
        super().__init__(child=label, tooltip_text=heading.text)
        self.line = heading.line


class Outline(Gtk.Popover):
    """Filled each time it opens (by `get_headings`), with the cursor's section selected."""

    def __init__(
        self,
        get_headings: Callable[[], tuple[list[Heading], int | None]],
        on_pick: Callable[[int], None],
    ) -> None:
        super().__init__()
        self._get_headings = get_headings
        self._on_pick = on_pick
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self._on_activated)
        placeholder = Gtk.Label(
            label="No headings yet.\nStart a line with # and a space.",
            justify=Gtk.Justification.CENTER,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )
        placeholder.add_css_class("dim-label")
        self.list.set_placeholder(placeholder)
        self.set_child(
            Gtk.ScrolledWindow(
                child=self.list,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                propagate_natural_height=True,
                propagate_natural_width=True,
                max_content_height=MAX_HEIGHT,
                min_content_width=240,
            )
        )
        self.connect("show", self._on_show)
        self.connect("map", self._on_map)

    def _on_show(self, _popover) -> None:
        headings, current = self._get_headings()
        self.list.remove_all()
        for heading in headings:
            self.list.append(OutlineRow(heading))
        row = self.list.get_row_at_index(current) if current is not None else None
        self.list.select_row(row)

    def _on_map(self, _popover) -> None:
        # Arrow keys and Enter work straight away, starting from the cursor's section.
        # A turn later: while mapping, the popover gives its own focus to the scroller.
        def focus() -> bool:
            row = self.list.get_selected_row() or self.list.get_row_at_index(0)
            if row is not None and self.get_visible():
                row.grab_focus()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(focus)

    def _on_activated(self, _list, row: OutlineRow) -> None:
        line = row.line
        self.popdown()
        # After the popover has handed focus back, so the editor keeps it.
        GLib.idle_add(lambda: (self._on_pick(line), GLib.SOURCE_REMOVE)[1])
