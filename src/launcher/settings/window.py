"""The Launcher Settings window: General, Shortcuts, Apps, Quicklinks, Snippets,
Clipboard and Files pages.

All changes go through ConfigWriter, which validates the whole config before writing,
so nothing done here can leave config.toml broken. The running launcher picks the
change up through its own file monitor within ~200 ms.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID, OBJECT_PATH, launching, paths, shortcuts  # noqa: E402
from ..clipboard_store import read_counts  # noqa: E402
from ..config import Config, ConfigError  # noqa: E402
from ..config_writer import ConfigWriter  # noqa: E402
from ..favicons import domain_of  # noqa: E402
from ..importers import espanso_base, import_espanso, read_espanso  # noqa: E402
from ..providers.apps import AppEntry, load_apps  # noqa: E402
from .dialogs import (  # noqa: E402
    AppDialog,
    AppPickerDialog,
    HotkeyRow,
    QuicklinkDialog,
    SnippetDialog,
    accel_label,
    action_row,
    app_icon,
    switch_row,
)

log = logging.getLogger(__name__)

SAVE_DELAY_MS = 500  # spin buttons: save once the value settles
RELOAD_DELAY_MS = 300


class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Launcher Settings")
        self.set_default_size(1000, 720)
        self.writer = ConfigWriter(paths.config_file())
        self.config = Config()
        self._apps: list[AppEntry] | None = None
        self._system: tuple[float, dict[str, str]] | None = None
        self._written: dict[str, float] = {}  # file name -> mtime of our last write
        self._reload_source = 0

        self._banner = Adw.Banner(button_label="Open Config File")
        self._banner.connect("button-clicked", lambda _b: self.open_config_file())
        self._stack = Adw.ViewStack()
        header = Adw.HeaderBar(
            title_widget=Adw.ViewSwitcher(stack=self._stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        )
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self._banner)
        toolbar.set_content(self._stack)
        self._toasts = Adw.ToastOverlay(child=toolbar)
        self.set_content(self._toasts)

        self._pages = [
            GeneralPage(self),
            ShortcutsPage(self),
            AppsPage(self),
            QuicklinksPage(self),
            SnippetsPage(self),
            ClipboardPage(self),
            FilesPage(self),
        ]
        for page in self._pages:
            self._stack.add_titled_with_icon(page, page.key, page.get_title(), page.get_icon_name())

        self._load()
        directory = Gio.File.new_for_path(str(paths.config_dir()))
        self._monitor = directory.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._monitor.connect("changed", self._on_config_dir_changed)

    # --- data used by pages and dialogs ------------------------------------------------

    def app_catalog(self) -> list[AppEntry]:
        if self._apps is None:
            self._apps = load_apps()
        return self._apps

    def app_by_id(self, app_id: str) -> AppEntry | None:
        return next((a for a in self.app_catalog() if a.id == app_id), None)

    def system_bindings(self) -> dict[str, str]:
        """Shortcuts used outside the launcher (cached briefly: it reads ~200 settings)."""
        if self._system is None or time.monotonic() - self._system[0] > 5:
            self._system = (time.monotonic(), shortcuts.system_bindings(include_owned=False))
        return self._system[1]

    def save(self, change: Callable[[ConfigWriter], Config]) -> str | None:
        """Apply a change through the validating writer. Returns an error message or None."""
        try:
            self.config = change(self.writer)
        except ConfigError as e:
            return str(e)
        except OSError as e:
            return f"Could not write the config file: {e}"
        self._remember_writes()
        self._refresh_pages()
        return None

    def _remember_writes(self) -> None:
        for path in (self.writer.path, self.writer.snippets_path):
            self._written[path.name] = _mtime(path)

    def save_or_toast(self, change: Callable[[ConfigWriter], Config]) -> None:
        if error := self.save(change):
            self.toast(f"Not saved: {error}")
            self._refresh_pages()  # put the controls back to what the file says

    def toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=message, timeout=6))

    def open_config_file(self) -> None:
        context = Gdk.Display.get_default().get_app_launch_context()
        try:
            launching.open_file(str(self.writer.path), context)
        except Exception as e:
            self.toast(f"Could not open the config file: {e}")

    def open_item(self, item: str) -> None:
        """Jump to an app, quicklink or snippet (from Ctrl+E in the launcher)."""
        kind, _, key = item.partition(":")

        def show() -> bool:
            if kind == "app":
                self._stack.set_visible_child_name("apps")
                AppDialog(self, key).present()
            elif kind == "quicklink":
                self._stack.set_visible_child_name("quicklinks")
                exists = any(q.name.casefold() == key.casefold() for q in self.config.quicklinks)
                QuicklinkDialog(self, key if exists else None).present()
            elif kind == "snippet":
                self._stack.set_visible_child_name("snippets")
                exists = any(s.name.casefold() == key.casefold() for s in self.config.snippets)
                SnippetDialog(self, key if exists else None, new_name=key).present()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(show)  # after the window is mapped, so the dialog has a parent

    # --- loading -----------------------------------------------------------------------

    def _load(self) -> None:
        try:
            self.config = self.writer.load()
        except ConfigError as e:
            # Hand-edited into an invalid state: show why and block editing until fixed,
            # rather than risk writing over the user's half-finished edit.
            self._banner.set_title(f"The config file has an error, so editing is paused: {e}")
            self._banner.set_revealed(True)
            self._stack.set_sensitive(False)
            return
        self._banner.set_revealed(False)
        self._stack.set_sensitive(True)
        self._remember_writes()
        self._refresh_pages()

    def _refresh_pages(self) -> None:
        for page in self._pages:
            page.refresh(self.config)

    def _on_config_dir_changed(self, _monitor, file, other, _event) -> None:
        names = {self.writer.path.name, self.writer.snippets_path.name}
        if file.get_basename() not in names and (
            other is None or other.get_basename() not in names
        ):
            return
        if self._reload_source:
            GLib.source_remove(self._reload_source)
        self._reload_source = GLib.timeout_add(RELOAD_DELAY_MS, self._reload_if_external)

    def _reload_if_external(self) -> bool:
        self._reload_source = 0
        paths_ = (self.writer.path, self.writer.snippets_path)
        if any(_mtime(p) != self._written.get(p.name) for p in paths_):
            log.info("config files changed outside the settings window; reloading")
            self._load()
        return GLib.SOURCE_REMOVE


def _mtime(path: Path) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


# --- pages ---------------------------------------------------------------------------


class _Page(Adw.PreferencesPage):
    key = ""

    def __init__(self, window: SettingsWindow, title: str, icon: str) -> None:
        super().__init__(title=title, icon_name=icon)
        self.window = window
        self._groups: list[Adw.PreferencesGroup] = []
        self._loading = False
        self._pending: dict[str, int] = {}

    def refresh(self, config: Config) -> None:
        raise NotImplementedError

    def _replace_groups(self, groups: list[Adw.PreferencesGroup]) -> None:
        for group in self._groups:
            self.remove(group)
        self._groups = groups
        for group in groups:
            self.add(group)

    def _save_later(self, key: str, change: Callable[[ConfigWriter], Config]) -> None:
        """Debounced save for controls that change in small steps (spin rows)."""
        if self._loading:
            return
        if source := self._pending.pop(key, 0):
            GLib.source_remove(source)

        def fire() -> bool:
            self._pending.pop(key, None)
            self.window.save_or_toast(change)
            return GLib.SOURCE_REMOVE

        self._pending[key] = GLib.timeout_add(SAVE_DELAY_MS, fire)

    def _save_now(self, change: Callable[[ConfigWriter], Config]) -> None:
        if not self._loading:
            self.window.save_or_toast(change)


class GeneralPage(_Page):
    key = "general"

    def __init__(self, window: SettingsWindow) -> None:
        super().__init__(window, "General", "preferences-system-symbolic")
        group = Adw.PreferencesGroup(title="Launcher Window")
        self._width = Adw.SpinRow.new_with_range(400, 1600, 10)
        self._width.set_title("Width")
        self._width.set_subtitle("In pixels")
        self._rows = Adw.SpinRow.new_with_range(1, 20, 1)
        self._rows.set_title("Results Shown")
        self._hide = switch_row(
            title="Hide When Another Window Is Focused",
            subtitle="Like clicking outside a popup",
        )
        self._favicons = switch_row(
            title="Website Icons for Quicklinks",
            subtitle="Downloaded once from Google's favicon service and cached",
        )
        for row in (self._width, self._rows, self._hide, self._favicons):
            group.add(row)
        self._width.connect("notify::value", lambda r, _p: self._spin("width", r))
        self._rows.connect("notify::value", lambda r, _p: self._spin("max_results", r))
        self._hide.connect("notify::active", lambda r, _p: self._switch("hide_on_focus_loss", r))
        self._favicons.connect("notify::active", lambda r, _p: self._switch("favicons", r))
        self.add(group)

        file_group = Adw.PreferencesGroup(
            title="Config File",
            description="Everything here is stored in this file, which you can also edit "
            "by hand. Changes apply as soon as it is saved.",
        )
        row = action_row(title=str(paths.config_file()).replace(str(Path.home()), "~"))
        row.add_css_class("property")
        button = Gtk.Button(label="Open in Text Editor", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: window.open_config_file())
        row.add_suffix(button)
        file_group.add(row)
        self.add(file_group)

    def refresh(self, config: Config) -> None:
        self._loading = True
        self._width.set_value(config.ui.width)
        self._rows.set_value(config.ui.max_results)
        self._hide.set_active(config.ui.hide_on_focus_loss)
        self._favicons.set_active(config.ui.favicons)
        self._loading = False

    def _spin(self, key: str, row: Adw.SpinRow) -> None:
        value = int(row.get_value())
        self._save_later(key, lambda w: w.set_value("ui", key, value))

    def _switch(self, key: str, row: Adw.SwitchRow) -> None:
        value = row.get_active()
        self._save_now(lambda w: w.set_value("ui", key, value))


class ShortcutsPage(_Page):
    key = "shortcuts"
    _MODES = {
        "launcher": ("Open the Launcher", "Apps, quicklinks and web search"),
        "files": ("File Search", "Search the folders set on the Files page"),
        "clipboard": ("Clipboard History", "Paste something you copied earlier"),
        "clipboard_pause": ("Pause Clipboard Recording", "Toggle; nothing copied is saved"),
        "snippets": ("Snippets", "Search your snippets and paste one"),
    }

    def __init__(self, window: SettingsWindow) -> None:
        super().__init__(window, "Shortcuts", "preferences-desktop-keyboard-shortcuts-symbolic")

    def refresh(self, config: Config) -> None:
        modes = Adw.PreferencesGroup(
            title="Launcher Shortcuts",
            description="Checked against GNOME's own shortcuts and your extensions before "
            "they are saved.",
        )
        for mode, (title, subtitle) in self._MODES.items():
            row = HotkeyRow(
                self.window,
                title,
                f"the launcher's {mode} shortcut",
                getattr(config.shortcuts, mode),
                lambda key, mode=mode: self._save_now(
                    lambda w: w.set_value("shortcuts", mode, key)
                ),
                subtitle=subtitle,
            )
            modes.add(row)

        items = Adw.PreferencesGroup(
            title="App, Quicklink and Snippet Hotkeys",
            description="Set these on the Apps, Quicklinks and Snippets pages, or with Ctrl+E "
            "in the launcher.",
        )
        count = 0
        for app_id, app in config.apps.items():
            if app.hotkey:
                entry = self.window.app_by_id(app_id)
                items.add(
                    self._item_row(entry.name if entry else app_id, app.hotkey, f"app:{app_id}")
                )
                count += 1
        for link in config.quicklinks:
            if link.hotkey:
                items.add(self._item_row(link.name, link.hotkey, f"quicklink:{link.name}"))
                count += 1
        for snippet in config.snippets:
            if snippet.hotkey:
                items.add(self._item_row(snippet.name, snippet.hotkey, f"snippet:{snippet.name}"))
                count += 1
        if not count:
            items.add(action_row(title="No item hotkeys yet", sensitive=False))
        self._replace_groups([modes, items])

    def _item_row(self, title: str, hotkey: str, item: str) -> Adw.ActionRow:
        row = action_row(title=title, activatable=True)
        row.add_suffix(Gtk.ShortcutLabel(accelerator=hotkey, valign=Gtk.Align.CENTER))
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda _r: self.window.open_item(item))
        return row


class AppsPage(_Page):
    key = "apps"

    def __init__(self, window: SettingsWindow) -> None:
        super().__init__(window, "Apps", "view-app-grid-symbolic")

    def refresh(self, config: Config) -> None:
        group = Adw.PreferencesGroup(
            title="App Aliases and Hotkeys",
            description="Tip: in the launcher, select an app and press Ctrl+E.",
        )
        entries = sorted(
            config.apps.items(),
            key=lambda item: (
                self.window.app_by_id(item[0]) or AppEntry(item[0], item[0])
            ).name.casefold(),
        )
        for app_id, settings in entries:
            app = self.window.app_by_id(app_id)
            parts = []
            if settings.alias:
                parts.append(f"alias “{settings.alias}”")
            if settings.hotkey:
                parts.append(accel_label(settings.hotkey))
            if app is None:
                parts.append("not installed")
            row = action_row(
                title=app.name if app else app_id,
                subtitle=" · ".join(parts),
                activatable=True,
            )
            row.add_prefix(app_icon(app))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect(
                "activated", lambda _r, app_id=app_id: AppDialog(self.window, app_id).present()
            )
            group.add(row)
        add = Adw.ButtonRow(title="Add App…", start_icon_name="list-add-symbolic")
        add.connect(
            "activated",
            lambda _r: AppPickerDialog(
                self.window, lambda app_id: AppDialog(self.window, app_id).present()
            ).present(),
        )
        group.add(add)
        self._replace_groups([group])


class QuicklinksPage(_Page):
    key = "quicklinks"

    def __init__(self, window: SettingsWindow) -> None:
        super().__init__(window, "Quicklinks", "web-browser-symbolic")

    def refresh(self, config: Config) -> None:
        group = Adw.PreferencesGroup(
            title="Quicklinks and Web Searches",
            description="Order matters for searches offered under the results.",
        )
        links = config.quicklinks
        for i, link in enumerate(links):
            parts = [f"“{link.alias}”"] if link.alias else []
            parts.append(link.url)
            if link.hotkey:
                parts.append(accel_label(link.hotkey))
            row = action_row(title=link.name, subtitle=" · ".join(parts), activatable=True)
            row.set_subtitle_lines(1)
            row.add_prefix(_link_icon(link.url, link.icon))
            if link.fallback:
                badge = Gtk.Label(label="search fallback", valign=Gtk.Align.CENTER)
                badge.add_css_class("caption")
                badge.add_css_class("dim-label")
                row.add_suffix(badge)
            for icon, delta, enabled in (
                ("go-up-symbolic", -1, i > 0),
                ("go-down-symbolic", 1, i < len(links) - 1),
            ):
                button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, sensitive=enabled)
                button.add_css_class("flat")
                button.connect(
                    "clicked",
                    lambda _b, name=link.name, delta=delta: self._save_now(
                        lambda w: w.move_quicklink(name, delta)
                    ),
                )
                row.add_suffix(button)
            row.connect(
                "activated", lambda _r, name=link.name: QuicklinkDialog(self.window, name).present()
            )
            group.add(row)
        add = Adw.ButtonRow(title="Add Quicklink…", start_icon_name="list-add-symbolic")
        add.connect("activated", lambda _r: QuicklinkDialog(self.window, None).present())
        group.add(add)
        self._replace_groups([group])


class SnippetsPage(_Page):
    key = "snippets"

    def __init__(self, window: SettingsWindow) -> None:
        super().__init__(window, "Snippets", "insert-text-symbolic")

    def refresh(self, config: Config) -> None:
        group = Adw.PreferencesGroup(
            title="Snippets",
            description="Paste them from the launcher (Snippets shortcut, or search by name). "
            "A trigger also expands as you type, anywhere.",
        )
        for snippet in config.snippets:
            first = next((ln.strip() for ln in snippet.body.splitlines() if ln.strip()), "")
            parts = [p for p in (snippet.trigger, snippet.alias and f"“{snippet.alias}”") if p]
            if snippet.hotkey:
                parts.append(accel_label(snippet.hotkey))
            parts.append(first)
            row = action_row(title=snippet.name, subtitle=" · ".join(parts), activatable=True)
            row.set_subtitle_lines(1)
            row.add_prefix(Gtk.Image(icon_name="insert-text-symbolic"))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect(
                "activated",
                lambda _r, name=snippet.name: SnippetDialog(self.window, name).present(),
            )
            group.add(row)
        add = Adw.ButtonRow(title="Add Snippet…", start_icon_name="list-add-symbolic")
        add.connect("activated", lambda _r: SnippetDialog(self.window, None).present())
        group.add(add)
        self._replace_groups([group, self._espanso_group(config)])

    def _espanso_group(self, config: Config) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Typed Expansion",
            description="Triggers are expanded by espanso, which the launcher keeps up to "
            "date. espanso restarts when snippets change, so a window may lose focus for a "
            "moment after you save.",
        )
        match_dir = paths.espanso_match_dir()
        if not match_dir.is_dir():
            group.add(
                action_row(
                    title="espanso isn't set up",
                    subtitle="Install espanso to expand triggers as you type; pasting from "
                    "the launcher works without it.",
                )
            )
            return group
        with_trigger = sum(1 for s in config.snippets if s.trigger)
        status = action_row(
            title="Snippets given to espanso",
            subtitle=f"{with_trigger} with a trigger, in "
            + str(match_dir / "launcher.yml").replace(str(Path.home()), "~"),
        )
        status.add_css_class("property")
        group.add(status)
        try:
            importable = read_espanso(espanso_base(), config.snippets).snippets
        except (OSError, ValueError, ImportError):
            importable = []  # no base.yml, or not one we understand
        except Exception:
            log.exception("reading espanso's base.yml failed")
            importable = []
        if importable:
            count = len(importable)
            row = action_row(
                title=f"Move {count} snippet{'s' if count != 1 else ''} from espanso",
                subtitle="From espanso's base.yml, so you can edit them here. A backup is "
                "kept as base.yml.bak; matches that need espanso features stay there.",
            )
            button = Gtk.Button(label="Move Here", valign=Gtk.Align.CENTER)
            button.add_css_class("suggested-action")
            button.connect("clicked", lambda _b: self._import())
            row.add_suffix(button)
            group.add(row)
        return group

    def _import(self) -> None:
        moved: list = []
        error = self.window.save(
            lambda w: (moved.extend(import_espanso(w, espanso_base())), w.load())[1]
        )
        if error:
            self.window.toast(f"Not imported: {error}")
        else:
            self.window.toast(f"Moved {len(moved)} snippets from espanso")


def _link_icon(url: str, icon: str) -> Gtk.Image:
    image = Gtk.Image(pixel_size=24)
    if icon:
        spec = icon
    else:
        domain = domain_of(url)
        cached = paths.cache_dir() / "favicons" / f"{domain}.icon" if domain else None
        spec = str(cached) if cached and cached.exists() else "web-browser"
    try:
        image.set_from_gicon(Gio.Icon.new_for_string(spec))
    except GLib.Error:
        image.set_from_icon_name("web-browser")
    return image


class FilesPage(_Page):
    key = "files"

    def __init__(self, window: SettingsWindow) -> None:
        super().__init__(window, "Files", "folder-symbolic")
        options = Adw.PreferencesGroup(title="Options")
        self._depth = Adw.SpinRow.new_with_range(1, 32, 1)
        self._depth.set_title("Folder Depth")
        self._depth.set_subtitle("How many levels of subfolders to search")
        self._hidden = switch_row(title="Include Hidden Files", subtitle="Names starting with “.”")
        self._depth.connect("notify::value", self._on_depth)
        self._hidden.connect("notify::active", self._on_hidden)
        options.add(self._depth)
        options.add(self._hidden)
        self._options = options

    def refresh(self, config: Config) -> None:
        self._loading = True
        self._depth.set_value(config.files.max_depth)
        self._hidden.set_active(config.files.show_hidden)
        self._loading = False

        folders = Adw.PreferencesGroup(
            title="Folders to Search", description="Subfolders are included."
        )
        for folder in config.files.folders:
            row = action_row(title=folder)
            row.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
            row.add_suffix(
                self._remove_button(
                    lambda w, f=folder: w.set_value(
                        "files", "folders", [x for x in config.files.folders if x != f]
                    )
                )
            )
            folders.add(row)
        add = Adw.ButtonRow(title="Add Folder…", start_icon_name="list-add-symbolic")
        add.connect("activated", lambda _r: self._pick_folder())
        folders.add(add)

        skip = Adw.PreferencesGroup(
            title="Skip These Names",
            description="Files and folders with these names are left out anywhere below the "
            "folders above. * matches anything, e.g. *.part",
        )
        for pattern in config.files.exclude:
            row = action_row(title=pattern)
            row.add_suffix(
                self._remove_button(
                    lambda w, p=pattern: w.set_value(
                        "files", "exclude", [x for x in config.files.exclude if x != p]
                    )
                )
            )
            skip.add(row)
        entry = Adw.EntryRow(title="Add a name or pattern", show_apply_button=True)
        entry.connect("apply", self._add_pattern)
        skip.add(entry)

        self._replace_groups([folders, skip, self._options])

    def _on_depth(self, row: Adw.SpinRow, _pspec) -> None:
        value = int(row.get_value())
        self._save_later("depth", lambda w: w.set_value("files", "max_depth", value))

    def _on_hidden(self, row: Adw.SwitchRow, _pspec) -> None:
        value = row.get_active()
        self._save_now(lambda w: w.set_value("files", "show_hidden", value))

    def _remove_button(self, change: Callable[[ConfigWriter], Config]) -> Gtk.Button:
        button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        button.set_tooltip_text("Remove")
        button.add_css_class("flat")
        button.connect("clicked", lambda _b: self._save_now(change))
        return button

    def _add_pattern(self, entry: Adw.EntryRow) -> None:
        pattern = entry.get_text().strip()
        current = list(self.window.config.files.exclude)
        if pattern and pattern not in current:
            self._save_now(lambda w: w.set_value("files", "exclude", current + [pattern]))

    def _pick_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Choose a Folder to Search")
        dialog.set_initial_folder(Gio.File.new_for_path(str(Path.home())))

        def done(d: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                folder = d.select_folder_finish(result)
            except GLib.Error:
                return  # cancelled
            path = folder.get_path() if folder else None
            if not path:
                return
            home = str(Path.home())
            shown = "~" + path[len(home) :] if path == home or path.startswith(home + "/") else path
            current = list(self.window.config.files.folders)
            if shown not in current:
                self._save_now(lambda w: w.set_value("files", "folders", current + [shown]))

        dialog.select_folder(self.window, None, done)


# --- clipboard ----------------------------------------------------------------------

CLEAR_CHOICES = (
    ("Delete the Last 15 Minutes", 15 * 60),
    ("Delete the Last Hour", 3600),
    ("Delete the Last 24 Hours", 86400),
    ("Delete All History", 0),
)


class ClipboardPage(_Page):
    key = "clipboard"

    def __init__(self, window: SettingsWindow) -> None:
        super().__init__(window, "Clipboard", "edit-paste-symbolic")
        limits = Adw.PreferencesGroup(
            title="Clipboard History",
            description="Older entries are deleted automatically; pinned ones never are.",
        )
        self._enabled = switch_row(
            title="Record Clipboard History", subtitle="Needs the Launcher Helper extension"
        )
        self._enabled.connect("notify::active", self._on_enabled)
        limits.add(self._enabled)
        self._spins = {}
        for key, title, subtitle, low, high, step in (
            ("max_entries", "Keep at Most", "Entries (pinned ones don't count)", 10, 10_000, 10),
            ("max_days", "Delete After", "Days", 1, 3650, 1),
            ("max_image_mb", "Largest Image", "MB; bigger images aren't recorded", 1, 200, 1),
        ):
            spin = Adw.SpinRow.new_with_range(low, high, step)
            spin.set_title(title)
            spin.set_subtitle(subtitle)
            spin.connect("notify::value", self._on_spin, key)
            limits.add(spin)
            self._spins[key] = spin
        self._limits = limits

        self._delete = Adw.PreferencesGroup(
            title="Delete History", description="Pinned entries are always kept."
        )
        self._count_row = action_row(title="Entries")
        self._count_row.add_css_class("property")
        self._delete.add(self._count_row)
        for title, seconds in CLEAR_CHOICES:
            button = Adw.ButtonRow(title=title)
            if seconds == 0:
                button.add_css_class("destructive-action")
            button.connect("activated", lambda _b, t=title, s=seconds: self._confirm_clear(t, s))
            self._delete.add(button)

    def refresh(self, config: Config) -> None:
        self._loading = True
        self._enabled.set_active(config.clipboard.enabled)
        for key, spin in self._spins.items():
            spin.set_value(getattr(config.clipboard, key))
        self._loading = False
        self._update_count()
        excluded = self._app_list_group(
            "Never Record From",
            "Password managers and anything else whose copies shouldn't be kept. "
            "Apps that mark copies as passwords are skipped anyway.",
            "exclude_apps",
            config.clipboard.exclude_apps,
        )
        terminals = self._app_list_group(
            "Paste With Ctrl+Shift+V",
            "Terminals use Ctrl+Shift+V to paste; everything else gets Ctrl+V.",
            "terminal_apps",
            config.clipboard.terminal_apps,
        )
        self._replace_groups([self._limits, self._delete, excluded, terminals])

    def _on_enabled(self, row: Adw.SwitchRow, _pspec) -> None:
        value = row.get_active()
        self._save_now(lambda w: w.set_value("clipboard", "enabled", value))

    def _on_spin(self, row: Adw.SpinRow, _pspec, key: str) -> None:
        value = int(row.get_value())
        self._save_later(key, lambda w: w.set_value("clipboard", key, value))

    # --- deleting ------------------------------------------------------------------------

    def _update_count(self) -> None:
        counts = read_counts(paths.data_dir())
        if counts is None:
            self._count_row.set_subtitle("Could not read the history")
        else:
            total, pinned = counts
            self._count_row.set_subtitle(f"{total} saved, {pinned} pinned")

    def _confirm_clear(self, title: str, seconds: int) -> None:
        span = "everything" if seconds == 0 else title.removeprefix("Delete ").lower()
        dialog = Adw.AlertDialog(
            heading=f"{title}?",
            body=f"Clipboard entries from {span} will be deleted for good. Pinned entries "
            "are kept.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response", lambda _d, response: response == "delete" and self._clear(seconds)
        )
        dialog.present(self.window)

    def _clear(self, seconds: int) -> None:
        """Ask the running launcher to delete: it owns the history and its open views."""
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        params = GLib.Variant("(sava{sv})", ("clear-clipboard", [GLib.Variant("x", seconds)], {}))

        def done(connection, result) -> None:
            try:
                connection.call_finish(result)
            except GLib.Error:
                self.window.toast("The launcher isn't running, so nothing was deleted.")
                return
            GLib.timeout_add(300, lambda: (self._update_count(), GLib.SOURCE_REMOVE)[1])

        bus.call(
            APP_ID, OBJECT_PATH, "org.gtk.Actions", "Activate", params, None,
            Gio.DBusCallFlags.NONE, 5000, None, done,
        )  # fmt: skip

    # --- app lists -----------------------------------------------------------------------

    def _app_list_group(
        self, title: str, description: str, key: str, values: tuple[str, ...]
    ) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=title, description=description)
        for value in values:
            app = self.window.app_by_id(value if value.endswith(".desktop") else value + ".desktop")
            row = action_row(title=app.name if app else value, subtitle=value if app else "")
            row.add_prefix(app_icon(app))
            button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            button.set_tooltip_text("Remove")
            button.add_css_class("flat")
            button.connect(
                "clicked",
                lambda _b, v=value: self._save_now(
                    lambda w: w.set_value("clipboard", key, [x for x in values if x != v])
                ),
            )
            row.add_suffix(button)
            group.add(row)

        def add(value: str) -> None:
            value = value.strip().removesuffix(".desktop")
            current = list(getattr(self.window.config.clipboard, key))
            if value and value not in current:
                self._save_now(lambda w: w.set_value("clipboard", key, current + [value]))

        pick = Adw.ButtonRow(title="Add App…", start_icon_name="list-add-symbolic")
        pick.connect("activated", lambda _r: AppPickerDialog(self.window, add).present())
        group.add(pick)
        entry = Adw.EntryRow(title="Or type a window class / app id", show_apply_button=True)
        entry.connect("apply", lambda e: add(e.get_text()))
        group.add(entry)
        return group
