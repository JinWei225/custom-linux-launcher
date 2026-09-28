"""The Notes window: a hideable sidebar of notes and folders, and the editor.

Saving is automatic: 1 s after typing stops, as soon as another window gets focus,
when switching notes and when the window closes. Nothing is written if the text hasn't
changed. Text an input method is still composing isn't in the buffer yet; it is saved
once committed.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from .. import paths  # noqa: E402
from ..config import Config, ConfigError, load_config  # noqa: E402
from ..notes_markdown import Heading, section_at  # noqa: E402
from ..notes_store import NotesError, NoteSession, NotesStore, flatten  # noqa: E402
from .editor import MarkdownEditor  # noqa: E402
from .outline import Outline  # noqa: E402
from .pdf import export_pdf  # noqa: E402
from .sidebar import Sidebar, SidebarRow  # noqa: E402

log = logging.getLogger(__name__)

AUTOSAVE_DELAY_MS = 1000
DISK_CHANGE_DELAY_MS = 300
NARROW = "max-width: 640sp"  # e.g. half a laptop screen beside slides: overlay sidebar

CSS = """
textview.notes-editor { font-size: 13pt; }
"""


def state_file() -> Path:
    return paths.state_dir() / "notes.json"


def _load_state() -> dict:
    try:
        data = json.loads(state_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _trash(path: Path) -> None:
    try:
        Gio.File.new_for_path(str(path)).trash(None)
    except GLib.Error as e:
        raise NotesError(f"could not move it to the Trash: {e.message}") from e


def notes_folder(config: Config) -> Path:
    return Path(os.path.expanduser(config.notes.folder))


class NotesWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Notes")
        self._state = _load_state()
        self.set_default_size(self._state.get("width", 1000), self._state.get("height", 720))
        if self._state.get("maximized"):
            self.maximize()
        try:
            config, _ = load_config(paths.config_file())
        except ConfigError as e:
            log.warning("config: %s (using the default notes folder)", e)
            config = Config()
        self.store = NotesStore(notes_folder(config), trash=_trash)
        self.store.ensure()
        self.session: NoteSession | None = None
        self._expanded: set[str] = set(self._state.get("expanded", []))
        self._loading = False
        self._save_source = 0
        self._disk_source = 0
        self._monitors: dict[str, Gio.FileMonitor] = {}
        self._surface: Gdk.Surface | None = None
        self._focused = False

        self._load_css()
        self._build()
        self._add_actions(app)
        self.refresh()
        last = self._state.get("last")
        if isinstance(last, str) and self.store.mtime(last) is not None:
            self.open_note(last)
        else:
            self._show_empty()

        self.connect("realize", self._on_realize)
        self.connect("close-request", self._on_close_request)

    # --- building ------------------------------------------------------------------------

    def _load_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build(self) -> None:
        self.split = Adw.OverlaySplitView(min_sidebar_width=220, max_sidebar_width=320)
        self.split.set_show_sidebar(self._state.get("sidebar", True))

        # Sidebar
        sidebar_header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Notes"))
        new_folder = Gtk.Button(icon_name="folder-new-symbolic", tooltip_text="New Folder")
        new_folder.set_action_name("win.new-folder")
        new_folder.set_action_target_value(GLib.Variant("s", ""))
        new_note = Gtk.Button(icon_name="document-new-symbolic", tooltip_text="New Note (Ctrl+N)")
        new_note.set_action_name("win.new-note")
        sidebar_header.pack_start(new_note)
        sidebar_header.pack_end(new_folder)
        self.sidebar = Sidebar(self.open_note, self._toggle_folder, self._row_menu)
        sidebar_view = Adw.ToolbarView(content=self.sidebar)
        sidebar_view.add_top_bar(sidebar_header)
        self.split.set_sidebar(sidebar_view)

        # Content
        self._title = Adw.WindowTitle(title="Notes")
        header = Adw.HeaderBar(title_widget=self._title)
        toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Sidebar (F9)")
        self.split.bind_property(
            "show-sidebar",
            toggle,
            "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        header.pack_start(toggle)
        self._menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Menu")
        header.pack_end(self._menu_button)
        self._outline = Outline(self._outline_headings, self._go_to_heading)
        self._outline_button = Gtk.MenuButton(
            icon_name="view-list-symbolic",
            tooltip_text="Outline (Ctrl+Shift+O)",
            popover=self._outline,
            sensitive=False,
        )
        header.pack_end(self._outline_button)

        self._banner = Adw.Banner(button_label="Reload")
        self._banner.connect("button-clicked", lambda _b: self._reload_from_disk())

        self.editor = MarkdownEditor()
        self.editor.link_handler = self._open_link
        self.editor.error_handler = self.toast
        self.buffer = self.editor.buffer
        self.buffer.connect("changed", self._on_changed)
        scroller = Gtk.ScrolledWindow(child=self.editor, vexpand=True)

        empty = Adw.StatusPage(
            icon_name="document-edit-symbolic",
            title="No Note Open",
            description="Pick a note in the sidebar, or start a new one with Ctrl+N.",
        )
        button = Gtk.Button(label="New Note", halign=Gtk.Align.CENTER)
        button.add_css_class("pill")
        button.add_css_class("suggested-action")
        button.set_action_name("win.new-note")
        empty.set_child(button)

        self._stack = Gtk.Stack()
        self._stack.add_named(empty, "empty")
        self._stack.add_named(scroller, "editor")
        content = Adw.ToolbarView(content=self._stack)
        content.add_top_bar(header)
        content.add_top_bar(self._banner)
        self.split.set_content(content)

        self._toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self._toasts)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(NARROW))
        narrow.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(narrow)

    def _add_actions(self, app: Adw.Application) -> None:
        actions: list[tuple[str, str | None, Callable]] = [
            ("new-note", None, lambda _p: self.new_note()),
            ("new-note-in", "s", lambda p: self.new_note(folder=p.get_string())),
            ("new-folder", "s", lambda p: self._ask_new_folder(p.get_string())),
            ("toggle-sidebar", None, lambda _p: self._toggle_sidebar()),
            ("pin-note", "s", lambda p: self._set_pinned(p.get_string(), True)),
            ("unpin-note", "s", lambda p: self._set_pinned(p.get_string(), False)),
            ("rename-note", "s", lambda p: self._ask_rename_note(p.get_string())),
            ("move-note", "s", lambda p: self._ask_move_note(p.get_string())),
            ("delete-note", "s", lambda p: self._delete_note(p.get_string())),
            ("rename-folder", "s", lambda p: self._ask_rename_folder(p.get_string())),
            ("delete-folder", "s", lambda p: self._confirm_delete_folder(p.get_string())),
            ("open-folder", None, lambda _p: self._open_notes_folder()),
            ("outline", None, lambda _p: self.show_outline()),
            ("export-pdf", None, lambda _p: self.ask_export_pdf()),
        ]
        for name, ptype, handler in actions:
            action = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
            action.connect("activate", lambda _a, p, h=handler: h(p))
            self.add_action(action)
        app.set_accels_for_action("win.new-note", ["<Control>n"])
        app.set_accels_for_action("win.toggle-sidebar", ["F9"])
        app.set_accels_for_action("win.outline", ["<Control><Shift>o"])
        app.set_accels_for_action("win.export-pdf", ["<Control><Shift>e"])
        app.set_accels_for_action("window.close", ["<Control>w"])

    # --- menus ---------------------------------------------------------------------------

    def _note_menu(self, rel: str) -> Gio.Menu:
        menu = Gio.Menu()
        pinned = rel in self.store.pins()
        top = Gio.Menu()
        top.append_item(
            _item("Unpin" if pinned else "Pin", "unpin-note" if pinned else "pin-note", rel)
        )
        menu.append_section(None, top)
        edit = Gio.Menu()
        edit.append_item(_item("Rename…", "rename-note", rel))
        edit.append_item(_item("Move To…", "move-note", rel))
        menu.append_section(None, edit)
        danger = Gio.Menu()
        danger.append_item(_item("Move to Trash", "delete-note", rel))
        menu.append_section(None, danger)
        return menu

    def _folder_menu(self, rel: str) -> Gio.Menu:
        menu = Gio.Menu()
        create = Gio.Menu()
        create.append_item(_item("New Note Here", "new-note-in", rel))
        create.append_item(_item("New Folder Here…", "new-folder", rel))
        menu.append_section(None, create)
        edit = Gio.Menu()
        edit.append_item(_item("Rename…", "rename-folder", rel))
        edit.append_item(_item("Move to Trash", "delete-folder", rel))
        menu.append_section(None, edit)
        return menu

    def _row_menu(self, row: SidebarRow) -> Gio.MenuModel | None:
        if row.kind == "note":
            return self._note_menu(row.rel)
        if row.kind == "folder":
            return self._folder_menu(row.rel)
        return None

    def _update_header_menu(self) -> None:
        menu = self._note_menu(self.session.rel) if self.session else Gio.Menu()
        if self.session:
            export = Gio.Menu()
            export.append("Export to PDF…", "win.export-pdf")
            menu.prepend_section(None, export)
        general = Gio.Menu()
        general.append_item(_item("New Folder…", "new-folder", ""))
        general.append("Open Notes Folder", "win.open-folder")
        menu.append_section(None, general)
        self._menu_button.set_menu_model(menu)

    # --- sidebar -------------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the notes folder into the sidebar (and watch every folder in it)."""
        tree = self.store.tree()
        notes = flatten(tree)
        recent = sorted(notes, key=lambda n: n.mtime, reverse=True)
        self._expanded &= set(self.store.folders(tree))
        self.sidebar.rebuild(
            self.store.pinned(), recent, tree, self._expanded, self.session and self.session.rel
        )
        self._watch(self.store.folders(tree))
        self._update_header_menu()

    def _toggle_folder(self, rel: str) -> None:
        self._expanded ^= {rel}
        self.refresh()

    def _expand_to(self, rel: str) -> None:
        parts = Path(rel).parent.parts
        for i in range(1, len(parts) + 1):
            self._expanded.add("/".join(parts[:i]))

    def _toggle_sidebar(self) -> None:
        self.split.set_show_sidebar(not self.split.get_show_sidebar())

    # --- outline -------------------------------------------------------------------------

    def show_outline(self) -> None:
        if self.session is not None:
            self._outline_button.popup()

    def _outline_headings(self) -> tuple[list[Heading], int | None]:
        headings = self.editor.headings()
        return headings, section_at(headings, self.editor.cursor_line())

    def _go_to_heading(self, line: int) -> None:
        if self.session is not None:
            self.editor.go_to_line(line)

    # --- opening and saving --------------------------------------------------------------

    def open_note(self, rel: str) -> None:
        rel = self._relative(rel)
        if self.session is not None and self.session.rel == rel:
            self.editor.grab_focus()
            return
        self._leave_note()
        try:
            session = NoteSession(self.store, rel)
        except NotesError as e:
            self.toast(str(e))
            self.refresh()
            return
        self._show_session(session)

    def new_note(self, folder: str | None = None, text: str = "") -> None:
        """A new note in a folder (default: the open note's), opened for typing."""
        if folder is None:
            folder = self._current_folder()
        self._leave_note()
        try:
            note = self.store.create(folder, text)
            session = NoteSession(self.store, note.rel, created_empty=not text.strip())
        except (NotesError, OSError) as e:
            self.toast(f"Could not create a note: {e}")
            return
        self._show_session(session, cursor_at_end=True)

    def _show_session(self, session: NoteSession, cursor_at_end: bool = False) -> None:
        self.session = session
        self._loading = True
        self.editor.load_text(session.saved_text)  # not undoable, never rewritten on load
        self._loading = False
        where = self.buffer.get_end_iter() if cursor_at_end else self.buffer.get_start_iter()
        self.buffer.place_cursor(where)
        self._banner.set_revealed(False)
        self._stack.set_visible_child_name("editor")
        self._expand_to(session.rel)
        self._state["last"] = session.rel
        self._update_title()
        self.refresh()
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self.editor.grab_focus()

    def _show_empty(self) -> None:
        self.session = None
        self._stack.set_visible_child_name("empty")
        self._update_title()
        self._update_header_menu()

    def _leave_note(self) -> None:
        """Save the open note (or drop it if it was new and stayed empty) and close it."""
        if self.session is None:
            return
        self.save_now()
        if self.session is not None and self.session.close(self._text()):
            log.debug("discarded empty new note %s", self.session.rel)
        self.session = None

    def save_now(self) -> bool:
        """Save the open note if its text changed. False if saving failed."""
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        if self.session is None:
            return True
        old = self.session.rel
        try:
            changed = self.session.save(self._text())
        except (NotesError, OSError) as e:
            log.error("saving %s failed: %s", old, e)
            self.toast(f"Not saved: {e}")
            return False
        if changed:
            log.debug("saved %s", self.session.rel)
            self._banner.set_revealed(False)
            if self.session.rel != old:
                self._state["last"] = self.session.rel
            self._update_title()
            self.refresh()
        return True

    def _on_changed(self, _buffer: Gtk.TextBuffer) -> None:
        if self._loading or self.session is None:
            return
        if self._save_source:
            GLib.source_remove(self._save_source)

        def fire() -> bool:
            self._save_source = 0
            self.save_now()
            return GLib.SOURCE_REMOVE

        self._save_source = GLib.timeout_add(AUTOSAVE_DELAY_MS, fire)

    def _text(self) -> str:
        return self.editor.text()  # the plain markdown, hidden markers included

    def _update_title(self) -> None:
        """Header title, and where the editor finds (and saves) this note's pictures.
        Called whenever the open note is opened, renamed or moved."""
        self._outline_button.set_sensitive(self.session is not None)
        if self.session is None:
            self.set_title("Notes")
            self._title.set_title("Notes")
            self._title.set_subtitle("")
            self.editor.base_dir = None
            return
        path = self.store.path(self.session.rel)
        self.editor.base_dir = path.parent
        self.editor.note_stem = path.stem
        self._title.set_title(self.session.title)
        self.set_title(f"{self.session.title} – Notes")  # Alt+Tab and the overview
        folder = str(Path(self.session.rel).parent)
        self._title.set_subtitle("" if folder == "." else folder)

    def _current_folder(self) -> str:
        if self.session is None:
            return ""
        folder = str(Path(self.session.rel).parent)
        return "" if folder == "." else folder

    def _relative(self, rel: str) -> str:
        """Accept an absolute path inside the notes folder too (from `--open`)."""
        path = Path(os.path.expanduser(rel))
        if path.is_absolute():
            try:
                return path.resolve().relative_to(self.store.root.resolve()).as_posix()
            except ValueError:
                return rel
        return rel

    # --- focus and closing ----------------------------------------------------------------

    def _on_realize(self, *_args) -> None:
        surface = self.get_surface()
        if surface is self._surface:
            return
        self._surface = surface
        surface.connect("notify::state", self._on_surface_state)

    def _on_surface_state(self, surface: Gdk.Toplevel, _pspec) -> None:
        # The compositor's "focused window" state: unlike keyboard focus it stays set
        # while an input method's candidate popup or a Shell popup is open.
        focused = bool(surface.get_state() & Gdk.ToplevelState.FOCUSED)
        if focused == self._focused:
            return
        self._focused = focused
        if not focused:
            log.debug("another window got focus: saving")
            self.save_now()

    def _on_close_request(self, *_args) -> bool:
        self._leave_note()
        self._save_state()
        return False  # let it close

    def _save_state(self) -> None:
        width, height = self.get_default_size()
        state = {
            **self._state,
            "width": width,
            "height": height,
            "maximized": self.is_maximized(),
            "sidebar": self.split.get_show_sidebar() or self.split.get_collapsed(),
            "expanded": sorted(self._expanded),
        }
        try:
            state_file().parent.mkdir(parents=True, exist_ok=True)
            state_file().write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError as e:
            log.warning("cannot save the notes window state: %s", e)

    # --- changes made by other apps -------------------------------------------------------

    def _watch(self, folders: list[str]) -> None:
        wanted = set(folders)
        for rel in set(self._monitors) - wanted:
            self._monitors.pop(rel).cancel()
        for rel in wanted - set(self._monitors):
            directory = Gio.File.new_for_path(str(self.store.root / rel))
            try:
                monitor = directory.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            except GLib.Error as e:
                log.warning("cannot watch %s: %s", rel or "the notes folder", e.message)
                continue
            monitor.connect("changed", self._on_disk_event)
            self._monitors[rel] = monitor

    def _on_disk_event(self, _monitor, file: Gio.File, _other, _event) -> None:
        name = file.get_basename() or ""
        if name.startswith(".") and name != ".notes.json":
            return  # our own temporary files
        if self._disk_source:
            GLib.source_remove(self._disk_source)
        self._disk_source = GLib.timeout_add(DISK_CHANGE_DELAY_MS, self._on_disk_changed)

    def _on_disk_changed(self) -> bool:
        self._disk_source = 0
        if self.session is not None:
            change = self.session.external_change()
            if change == "gone":
                if self.session.is_dirty(self._text()):
                    self.toast("This note was moved or deleted by another app; saving it again")
                    self.save_now()
                else:
                    self.toast(f"“{self.session.title}” was moved or deleted by another app")
                    self._show_empty()
            elif change == "changed":
                if self.session.is_dirty(self._text()):
                    self._banner.set_title(
                        "Another app changed this note. Reload to see its version, or keep "
                        "typing to keep yours."
                    )
                    self._banner.set_revealed(True)
                else:
                    self._reload_from_disk()
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _reload_from_disk(self) -> None:
        if self.session is None:
            return
        offset = self.buffer.get_property("cursor-position")
        try:
            text = self.session.reload()
        except NotesError as e:
            self.toast(str(e))
            return
        self._loading = True
        self.editor.load_text(text)
        self._loading = False
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(min(offset, len(text))))
        self._banner.set_revealed(False)
        self._update_title()

    # --- PDF -------------------------------------------------------------------------------

    def ask_export_pdf(self) -> None:
        if self.session is None:
            return
        self.save_now()
        dialog = Gtk.FileDialog(
            title="Export to PDF", initial_name=f"{Path(self.session.rel).stem}.pdf"
        )
        pdfs = Gtk.FileFilter(name="PDF documents")
        pdfs.add_mime_type("application/pdf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(pdfs)
        dialog.set_filters(filters)
        folder = self._state.get("export_folder") or GLib.get_user_special_dir(
            GLib.UserDirectory.DIRECTORY_DOCUMENTS
        )
        if folder and Path(folder).is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(folder))
        dialog.save(self, None, self._on_export_chosen)

    def _on_export_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return  # cancelled
        if file is not None and file.get_path():
            path = Path(file.get_path())
            self.export_pdf(path if path.suffix.lower() == ".pdf" else path.with_suffix(".pdf"))

    def export_pdf(self, path: Path) -> bool:
        """Write the open note to `path` as a PDF; a toast offers to open it."""
        if self.session is None:
            return False
        font = Pango.FontDescription.from_string(
            Gtk.Settings.get_default().get_property("gtk-font-name") or "Sans"
        ).get_family()
        try:
            pages = export_pdf(
                self._text(),
                path,
                title=self.session.title,
                image_path=self.editor.image_path,
                font=font or "Sans",
            )
        except (OSError, cairo.Error, GLib.Error) as e:
            log.error("exporting %s to %s failed: %s", self.session.rel, path, e)
            self.toast(f"Could not export: {e}")
            return False
        log.debug("exported %s to %s (%d pages)", self.session.rel, path, pages)
        self._state["export_folder"] = str(path.parent)
        toast = Adw.Toast(title=f"Exported “{path.name}”", button_label="Open", timeout=5)
        toast.connect(
            "button-clicked",
            lambda _t: Gtk.FileLauncher.new(Gio.File.new_for_path(str(path))).launch(
                self, None, None
            ),
        )
        self._toasts.add_toast(toast)
        return True

    # --- note and folder operations -------------------------------------------------------

    def _set_pinned(self, rel: str, pinned: bool) -> None:
        self._run(lambda: self.store.set_pinned(rel, pinned))

    def _ask_rename_note(self, rel: str) -> None:
        current = self.session.title if self.session and self.session.rel == rel else None
        try:
            title = current or self.store.note(rel).title
        except NotesError as e:
            self.toast(str(e))
            return

        def rename(new_title: str) -> None:
            is_open = self.session is not None and self.session.rel == rel
            if is_open:
                self.save_now()
                self.session = None
            new = self._run(lambda: self.store.rename_note(rel, new_title))
            if is_open:
                self.open_note(new or rel)

        self._ask_text(
            "Rename Note", "The title is the note's first line.", title, "Rename", rename
        )

    def _ask_move_note(self, rel: str) -> None:
        folders = self.store.folders()
        labels = ["Notes (top level)", *folders[1:]]
        dropdown = Gtk.DropDown.new_from_strings(labels)
        dialog = Adw.AlertDialog(heading="Move To", body=f"Move “{Path(rel).stem}” to:")
        dialog.set_extra_child(dropdown)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("move", "Move")
        dialog.set_response_appearance("move", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("move")

        def response(_d, answer: str) -> None:
            if answer != "move":
                return
            folder = folders[dropdown.get_selected()]
            if self.session is not None and self.session.rel == rel:
                self.save_now()
            new = self._run(lambda: self.store.move(rel, folder))
            if new and self.session is not None and self.session.rel == rel:
                self.session.rel = new
                self._state["last"] = new
                self._expand_to(new)
                self._update_title()
                self.refresh()

        dialog.connect("response", response)
        dialog.present(self)

    def _delete_note(self, rel: str) -> None:
        if self.session is not None and self.session.rel == rel:
            self.save_now()  # the Trash gets the latest text
            self._show_empty()
        title = Path(rel).stem
        if self._run(lambda: self.store.delete(rel)) is not False:
            self.toast(f"“{title}” moved to the Trash")

    def _ask_new_folder(self, parent: str) -> None:
        def create(name: str) -> None:
            new = self._run(lambda: self.store.create_folder(parent, name))
            if new:
                self._expanded.add(new)
                self._expand_to(new)  # and the folders it is in
                self.refresh()

        where = f"inside “{parent}”" if parent else "in your notes folder"
        self._ask_text("New Folder", f"A folder {where}.", "", "Create", create)

    def _ask_rename_folder(self, rel: str) -> None:
        def rename(name: str) -> None:
            is_inside = self.session is not None and self.session.rel.startswith(rel + "/")
            if is_inside:
                self.save_now()
            new = self._run(lambda: self.store.rename_folder(rel, name))
            if not new:
                return
            self._expanded = {new + e[len(rel) :] if e == rel or e.startswith(rel + "/") else e
                              for e in self._expanded}  # fmt: skip
            if is_inside and self.session is not None:
                self.session.rel = new + self.session.rel[len(rel) :]
                self._state["last"] = self.session.rel
                self._update_title()
            self.refresh()

        self._ask_text("Rename Folder", "", Path(rel).name, "Rename", rename)

    def _confirm_delete_folder(self, rel: str) -> None:
        count = sum(1 for n in self.store.all_notes() if n.rel.startswith(rel + "/"))
        notes = "1 note" if count == 1 else f"{count} notes"
        dialog = Adw.AlertDialog(
            heading=f"Move “{Path(rel).name}” to the Trash?",
            body=f"The folder and its {notes} can be restored from the Trash.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Move to Trash")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_d, answer: str) -> None:
            if answer != "delete":
                return
            if self.session is not None and self.session.rel.startswith(rel + "/"):
                self.save_now()
                self._show_empty()
            self._run(lambda: self.store.delete_folder(rel))

        dialog.connect("response", response)
        dialog.present(self)

    def _open_link(self, url: str) -> None:
        """Ctrl+click: web links in the browser, other notes here, files in their app."""
        if "://" in url or url.startswith(("mailto:", "www.")):
            web = url if not url.startswith("www.") else "https://" + url
            Gtk.UriLauncher.new(web).launch(self, None, None)
            return
        path = self.editor.image_path(url)
        if path is None or not path.exists():
            self.toast(f"“{url}” doesn't exist")
            return
        root = self.store.root.resolve()
        resolved = path.resolve()
        if resolved.suffix.lower() == ".md" and root in resolved.parents:
            self.open_note(resolved.relative_to(root).as_posix())
            return
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(resolved))).launch(self, None, None)

    def _open_notes_folder(self) -> None:
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(self.store.root))).launch(self)

    # --- helpers -----------------------------------------------------------------------------

    def _run(self, operation: Callable):
        """Run a store operation; toast its error. Returns its result, or False."""
        try:
            result = operation()
        except (NotesError, OSError) as e:
            self.toast(str(e) if isinstance(e, NotesError) else f"Could not do that: {e}")
            self.refresh()
            return False
        self.refresh()
        return result

    def _ask_text(
        self, heading: str, body: str, initial: str, ok: str, done: Callable[[str], None]
    ) -> None:
        entry = Gtk.Entry(text=initial, activates_default=True)
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", ok)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def response(_d, answer: str) -> None:
            text = entry.get_text().strip()
            if answer == "ok" and text:
                done(text)

        dialog.connect("response", response)
        dialog.present(self)
        entry.grab_focus()

    def toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=message, timeout=5))


def _item(label: str, action: str, target: str) -> Gio.MenuItem:
    item = Gio.MenuItem.new(label, None)
    item.set_action_and_target_value(f"win.{action}", GLib.Variant("s", target))
    return item
