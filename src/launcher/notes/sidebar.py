"""The notes sidebar: Pinned, Recent, then the folder tree.

A plain Gtk.ListBox rebuilt from the store on every change (a few hundred rows at
most), with indentation for depth and a right-click menu per row.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ..notes_store import Folder, Note  # noqa: E402

RECENT_LIMIT = 10
INDENT = 14


class SidebarRow(Gtk.ListBoxRow):
    def __init__(self, kind: str, rel: str, label: str, depth: int = 0, **props) -> None:
        super().__init__(**props)
        self.kind = kind  # "note" | "folder" | "header"
        self.rel = rel
        self.label = label
        self.depth = depth


class Sidebar(Gtk.ScrolledWindow):
    def __init__(
        self,
        on_open: Callable[[str], None],
        on_toggle_folder: Callable[[str], None],
        menu_for: Callable[[SidebarRow], Gio.MenuModel | None],
    ) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self._on_open = on_open
        self._on_toggle_folder = on_toggle_folder
        self._menu_for = menu_for
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self._activated)
        self.set_child(self.list)

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._right_click)
        self.list.add_controller(click)
        # The open right-click menu. It hangs off a row, so it must be detached before
        # that row goes away (rebuild) or the window closes (unrealize): GTK crashes
        # finalizing a row that still has a menu attached.
        self._popover: Gtk.PopoverMenu | None = None
        self._pending: tuple | None = None  # a rebuild held back while the menu is open
        self.connect("unrealize", lambda *_a: self._drop_popover())

    # --- building ------------------------------------------------------------------------

    def rebuild(
        self,
        pinned: list[Note],
        recent: list[Note],
        tree: Folder,
        expanded: set[str],
        current: str | None,
    ) -> None:
        if self._popover is not None:
            # A background refresh (autosave, another app's change) must not pull the
            # menu out from under the pointer: rebuild once it closes.
            self._pending = (pinned, recent, tree, expanded, current)
            return
        self._pending = None
        self.list.remove_all()
        if pinned:
            self._header("Pinned")
            for note in pinned:
                self._note(note, 0, icon="view-pin-symbolic")
        if recent:
            self._header("Recent")
            for note in recent[:RECENT_LIMIT]:
                self._note(note, 0)
        self._header("All Notes")
        self._folder_contents(tree, 0, expanded)
        self.select(current)

    def _header(self, title: str) -> None:
        row = SidebarRow("header", "", title, activatable=False, selectable=False)
        label = Gtk.Label(label=title, xalign=0, margin_top=10, margin_start=6)
        label.add_css_class("heading")
        label.add_css_class("dim-label")
        row.set_child(label)
        self.list.append(row)

    def _folder_contents(self, folder: Folder, depth: int, expanded: set[str]) -> None:
        for sub in folder.folders:
            is_open = sub.rel in expanded
            row = SidebarRow("folder", sub.rel, sub.name, depth)
            row.set_child(
                self._line(
                    sub.name,
                    depth,
                    "folder-open-symbolic" if is_open else "folder-symbolic",
                    arrow="pan-down-symbolic" if is_open else "pan-end-symbolic",
                )
            )
            self.list.append(row)
            if is_open:
                self._folder_contents(sub, depth + 1, expanded)
        for note in folder.notes:
            self._note(note, depth, in_tree=True)

    def _note(
        self, note: Note, depth: int, icon: str = "text-x-generic-symbolic", in_tree: bool = False
    ) -> None:
        row = SidebarRow("note", note.rel, note.title, depth)
        row.set_tooltip_text(note.rel)
        # In the tree, notes get an empty arrow slot so they line up with folder names.
        row.set_child(self._line(note.title, depth, icon, arrow="" if in_tree else None))
        self.list.append(row)

    @staticmethod
    def _line(text: str, depth: int, icon: str, arrow: str | None) -> Gtk.Box:
        box = Gtk.Box(spacing=6, margin_start=depth * INDENT)
        if arrow is not None:
            arrow_image = Gtk.Image(pixel_size=12)
            if arrow:
                arrow_image.set_from_icon_name(arrow)
            box.append(arrow_image)
        box.append(Gtk.Image(icon_name=icon))
        box.append(Gtk.Label(label=text, xalign=0, ellipsize=Pango.EllipsizeMode.END))
        return box

    def select(self, rel: str | None) -> None:
        """Highlight the open note (its first row: Pinned, Recent or the tree)."""
        if rel is None:
            self.list.unselect_all()
            return
        i = 0
        while (row := self.list.get_row_at_index(i)) is not None:
            if row.kind == "note" and row.rel == rel:
                self.list.select_row(row)
                return
            i += 1
        self.list.unselect_all()

    # --- events --------------------------------------------------------------------------

    def _activated(self, _list: Gtk.ListBox, row: SidebarRow) -> None:
        if row.kind == "note":
            self._on_open(row.rel)
        elif row.kind == "folder":
            self._on_toggle_folder(row.rel)

    def _right_click(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        row = self.list.get_row_at_y(int(y))
        if row is None or row.kind == "header":
            return
        menu = self._menu_for(row)
        if menu is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.list.select_row(row)
        # Attached to the row, not the list: a list box's remove_all() loops forever on
        # a child that isn't a row.
        self._drop_popover()
        ok, bounds = row.compute_bounds(self.list)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_has_arrow(False)
        popover.set_parent(row)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y - bounds.get_y()) if ok else 0, 1, 1
        popover.set_pointing_to(rect)
        # Detach once closed, after the chosen item's action has run.
        popover.connect("closed", lambda p: GLib.idle_add(self._menu_closed, p))
        self._popover = popover
        # Open it on the next turn of the main loop: a menu popped up in the frame it was
        # created in is sized before its items are measured, and then has to scroll.
        GLib.idle_add(self._show_popover, popover)

    def _show_popover(self, popover: Gtk.PopoverMenu) -> bool:
        if popover is self._popover:  # not dropped meanwhile
            popover.popup()
        return GLib.SOURCE_REMOVE

    def _menu_closed(self, popover: Gtk.PopoverMenu) -> bool:
        if popover is self._popover:
            self._drop_popover()
            if self._pending is not None:
                self.rebuild(*self._pending)
        return GLib.SOURCE_REMOVE

    def _drop_popover(self) -> None:
        """Close and detach the open menu, if any."""
        popover, self._popover = self._popover, None
        if popover is not None:
            popover.popdown()
            if popover.get_parent() is not None:
                popover.unparent()
