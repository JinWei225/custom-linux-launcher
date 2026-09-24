"""Dialogs and rows used by the settings pages."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import accel  # noqa: E402
from ..config import AppSettings, QuickLink, Snippet, hotkey_owners  # noqa: E402
from ..ranking import best_score  # noqa: E402

if TYPE_CHECKING:
    from .window import SettingsWindow

_MODIFIER_KEYS = {
    Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R,
    Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Meta_L,
    Gdk.KEY_Meta_R, Gdk.KEY_Hyper_L, Gdk.KEY_Hyper_R, Gdk.KEY_ISO_Level3_Shift,
    Gdk.KEY_Caps_Lock, Gdk.KEY_Num_Lock,
}  # fmt: skip
_RECORDED_MODS = (
    Gdk.ModifierType.CONTROL_MASK
    | Gdk.ModifierType.ALT_MASK
    | Gdk.ModifierType.SHIFT_MASK
    | Gdk.ModifierType.SUPER_MASK
)


def action_row(title: str = "", subtitle: str = "", **props) -> Adw.ActionRow:
    """ActionRow showing plain text: URLs and names may contain & or <, which markup
    would reject. Text is set after construction so markup is already off."""
    row = Adw.ActionRow(use_markup=False, **props)
    row.set_title(title)
    row.set_subtitle(subtitle)
    return row


def switch_row(title: str = "", subtitle: str = "", **props) -> Adw.SwitchRow:
    row = Adw.SwitchRow(use_markup=False, **props)
    row.set_title(title)
    row.set_subtitle(subtitle)
    return row


def accel_label(hotkey: str) -> str:
    if not hotkey:
        return ""
    ok, key, mods = Gtk.accelerator_parse(hotkey)
    return Gtk.accelerator_get_label(key, mods) if ok and key else hotkey


def hotkey_clash(window: SettingsWindow, hotkey: str, owner: str) -> str | None:
    """Who else already uses this hotkey (GNOME, an extension, or another config item)."""
    canonical = accel.normalize(hotkey)
    if canonical is None:
        return None
    for key, other in hotkey_owners(window.config):
        if other != owner and accel.same(key, hotkey):
            return other
    return window.system_bindings().get(canonical.casefold())


def _dialog(title: str, width: int = 460) -> tuple[Adw.Dialog, Adw.ToolbarView, Adw.HeaderBar]:
    dialog = Adw.Dialog(title=title, content_width=width)
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    toolbar.add_top_bar(header)
    dialog.set_child(toolbar)
    return dialog, toolbar, header


def _save_cancel(dialog: Adw.Dialog, header: Adw.HeaderBar, on_save: Callable[[], None]):
    header.set_show_end_title_buttons(False)
    header.set_show_start_title_buttons(False)
    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", lambda _b: dialog.close())
    save = Gtk.Button(label="Save")
    save.add_css_class("suggested-action")
    save.connect("clicked", lambda _b: on_save())
    header.pack_start(cancel)
    header.pack_end(save)
    return save


def _error_label() -> Gtk.Label:
    label = Gtk.Label(wrap=True, xalign=0, visible=False, margin_top=6)
    label.add_css_class("error")
    return label


def _show_error(label: Gtk.Label, message: str | None) -> None:
    label.set_text(message or "")
    label.set_visible(bool(message))


# --- shortcut recording ------------------------------------------------------------


class ShortcutDialog:
    """ "Press a shortcut" dialog. Calls on_done(accel) with "" for "remove"."""

    def __init__(
        self,
        window: SettingsWindow,
        title: str,
        owner: str,
        on_done: Callable[[str], None],
    ) -> None:
        self._window = window
        self._owner = owner
        self._on_done = on_done
        self.dialog, toolbar, _header = _dialog(title, 420)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        heading = Gtk.Label(label="Press the new shortcut")
        heading.add_css_class("title-3")
        hint = Gtk.Label(
            label="Use Super, Ctrl or Alt with a key.\n"
            "Esc cancels · Backspace removes the shortcut",
            justify=Gtk.Justification.CENTER,
        )
        hint.add_css_class("dim-label")
        self._preview = Gtk.ShortcutLabel(disabled_text="…", halign=Gtk.Align.CENTER)
        self._error = _error_label()
        self._error.set_justify(Gtk.Justification.CENTER)
        self._error.set_xalign(0.5)
        for widget in (heading, hint, self._preview, self._error):
            box.append(widget)
        toolbar.set_content(box)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.dialog.add_controller(keys)

    def present(self) -> None:
        self.dialog.present(self._window)

    def _on_key(self, controller, keyval: int, keycode: int, state: Gdk.ModifierType) -> bool:
        mods = state & _RECORDED_MODS
        if keyval in _MODIFIER_KEYS:
            return True
        if not mods and keyval == Gdk.KEY_Escape:
            self.dialog.close()
            return True
        if not mods and keyval == Gdk.KEY_BackSpace:
            self._finish("")
            return True
        # Record the unshifted key ("<Shift><Super>1", not "<Shift><Super>exclam").
        display = self.dialog.get_display()
        ok, base, *_ = display.translate_key(keycode, 0, controller.get_group())
        key = Gdk.keyval_to_lower(base if ok and base else keyval)
        hotkey = accel.normalize(Gtk.accelerator_name(key, mods)) or ""
        self._preview.set_accelerator(hotkey)
        if problem := accel.hotkey_problem(hotkey):
            _show_error(self._error, problem)
            return True
        if clash := hotkey_clash(self._window, hotkey, self._owner):
            _show_error(self._error, f"{accel_label(hotkey)} is already used by {clash}")
            return True
        self._finish(hotkey)
        return True

    def _finish(self, hotkey: str) -> None:
        self.dialog.close()
        self._on_done(hotkey)


class HotkeyRow(Adw.ActionRow):
    """Row showing a hotkey, with buttons to change or remove it."""

    def __init__(
        self,
        window: SettingsWindow,
        title: str,
        owner: str,
        hotkey: str,
        on_change: Callable[[str], None],
        subtitle: str = "",
    ) -> None:
        super().__init__(use_markup=False)
        self.set_title(title)
        self.set_subtitle(subtitle)
        self._window = window
        self._owner = owner
        self._on_change = on_change
        self.hotkey = hotkey
        self._label = Gtk.ShortcutLabel(disabled_text="Not set", valign=Gtk.Align.CENTER)
        self._clear = Gtk.Button(
            icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove"
        )
        self._clear.add_css_class("flat")
        self._clear.connect("clicked", lambda _b: self._set(""))
        edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        edit.set_tooltip_text("Change shortcut")
        edit.add_css_class("flat")
        edit.connect("clicked", lambda _b: self._record())
        self.add_suffix(self._label)
        self.add_suffix(self._clear)
        self.add_suffix(edit)
        self.set_activatable_widget(edit)
        self._update()

    def _record(self) -> None:
        ShortcutDialog(self._window, self.get_title(), self._owner, self._set).present()

    def _set(self, hotkey: str) -> None:
        self.hotkey = hotkey
        self._update()
        self._on_change(hotkey)

    def _update(self) -> None:
        self._label.set_accelerator(self.hotkey)
        self._clear.set_visible(bool(self.hotkey))


# --- apps -------------------------------------------------------------------------


def app_icon(app) -> Gtk.Image:
    image = Gtk.Image(pixel_size=32)
    try:
        if app is not None and app.icon:
            image.set_from_gicon(Gio.Icon.new_for_string(app.icon))
            return image
    except GLib.Error:
        pass
    image.set_from_icon_name("application-x-executable")
    return image


class AppPickerDialog:
    """Search the installed apps and pick one."""

    def __init__(self, window: SettingsWindow, on_pick: Callable[[str], None]) -> None:
        self._window = window
        self._on_pick = on_pick
        self.dialog, toolbar, _header = _dialog("Choose an App", 460)
        self.dialog.set_content_height(560)
        self._entry = Gtk.SearchEntry(placeholder_text="Search apps…", margin_start=12)
        self._entry.set_margin_end(12)
        self._entry.set_margin_top(6)
        self._entry.set_margin_bottom(6)
        self._entry.connect("search-changed", lambda _e: self._refresh())
        self._entry.connect("activate", lambda _e: self._pick_first())
        toolbar.add_top_bar(self._entry)
        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        self._list.set_margin_start(12)
        self._list.set_margin_end(12)
        self._list.set_margin_bottom(12)
        self._list.connect("row-activated", lambda _l, row: self._pick(row.app_id))
        toolbar.set_content(Gtk.ScrolledWindow(child=self._list, vexpand=True))
        self._refresh()

    def present(self) -> None:
        self.dialog.present(self._window)
        self._entry.grab_focus()

    def _refresh(self) -> None:
        query = self._entry.get_text().strip()
        apps = self._window.app_catalog()
        if query:
            scored = [(best_score(query, (a.name, a.id, a.executable)), a) for a in apps]
            apps = [a for s, a in sorted(scored, key=lambda p: -(p[0] or 0)) if s is not None]
        self._list.remove_all()
        for app in apps[:100]:
            row = action_row(title=app.name, subtitle=app.id, activatable=True)
            row.add_prefix(app_icon(app))
            row.app_id = app.id
            self._list.append(row)

    def _pick_first(self) -> None:
        row = self._list.get_row_at_index(0)
        if row is not None:
            self._pick(row.app_id)

    def _pick(self, app_id: str) -> None:
        self.dialog.close()
        self._on_pick(app_id)


class AppDialog:
    """Alias and hotkey for one app."""

    def __init__(self, window: SettingsWindow, app_id: str) -> None:
        self._window = window
        self._app_id = app_id
        current = window.config.apps.get(app_id, AppSettings())
        app = window.app_by_id(app_id)
        name = app.name if app else app_id
        self.dialog, toolbar, header = _dialog(name)
        _save_cancel(self.dialog, header, self._save)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            description="Type the alias in the launcher to put this app first. "
            "The hotkey launches it from anywhere."
        )
        self._alias = Adw.EntryRow(title="Alias", text=current.alias)
        self._alias.connect("changed", lambda _e: self._validate())
        self._alias.connect("entry-activated", lambda _e: self._save())
        self._hotkey = HotkeyRow(
            window, "Hotkey", f"app '{app_id}'", current.hotkey, lambda _k: self._validate()
        )
        group.add(self._alias)
        group.add(self._hotkey)
        self._error = _error_label()
        group.add(self._error)
        page.add(group)

        if app_id in window.config.apps:
            remove_group = Adw.PreferencesGroup()
            remove = Adw.ButtonRow(title="Remove Alias and Hotkey")
            remove.add_css_class("destructive-action")
            remove.connect("activated", lambda _r: self._remove())
            remove_group.add(remove)
            page.add(remove_group)
        toolbar.set_content(page)

    def present(self) -> None:
        self.dialog.present(self._window)
        self._alias.grab_focus()

    def _validate(self) -> bool:
        alias = self._alias.get_text().strip()
        error = None
        if any(ch.isspace() for ch in alias):
            error = "The alias can't contain spaces."
        if error:
            self._alias.add_css_class("error")
        else:
            self._alias.remove_css_class("error")
        _show_error(self._error, error)
        return error is None

    def _save(self) -> None:
        if not self._validate():
            return
        settings = AppSettings(self._alias.get_text().strip(), self._hotkey.hotkey)
        error = self._window.save(lambda w: w.set_app(self._app_id, settings))
        if error:
            _show_error(self._error, error)
        else:
            self.dialog.close()

    def _remove(self) -> None:
        if not self._window.save(lambda w: w.set_app(self._app_id, AppSettings())):
            self.dialog.close()


# --- quicklinks -----------------------------------------------------------------------


class QuicklinkDialog:
    """Add or edit one quicklink (plain link or {query} search)."""

    def __init__(self, window: SettingsWindow, name: str | None) -> None:
        self._window = window
        self._original = name
        link = next(
            (q for q in window.config.quicklinks if name and q.name.casefold() == name.casefold()),
            QuickLink(name="", url="https://"),
        )
        self.dialog, toolbar, header = _dialog("Edit Quicklink" if name else "New Quicklink", 500)
        _save_cancel(self.dialog, header, self._save)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            description="Put {query} in the URL to make it a search: "
            "typing the alias, a space and some words searches for them."
        )
        self._name = Adw.EntryRow(title="Name", text=link.name)
        self._url = Adw.EntryRow(title="URL", text=link.url)
        self._alias = Adw.EntryRow(title="Alias", text=link.alias)
        self._icon = Adw.EntryRow(title="Icon (optional: icon name or file path)", text=link.icon)
        self._fallback = switch_row(
            title="Offer for Any Search",
            subtitle="Show “Search <name> for …” under the results for anything you type",
            active=link.fallback,
        )
        self._hotkey = HotkeyRow(
            window,
            "Hotkey",
            f"quicklink '{name}'" if name else "quicklink (new)",
            link.hotkey,
            lambda _k: self._validate(),
            subtitle="Opens the link; for a search, opens the launcher with the alias typed",
        )
        for row in (self._name, self._url, self._alias, self._icon):
            row.connect("changed", lambda _e: self._validate())
            row.connect("entry-activated", lambda _e: self._save())
            group.add(row)
        group.add(self._fallback)
        group.add(self._hotkey)
        self._error = _error_label()
        group.add(self._error)
        page.add(group)

        if name:
            remove_group = Adw.PreferencesGroup()
            remove = Adw.ButtonRow(title="Delete Quicklink")
            remove.add_css_class("destructive-action")
            remove.connect("activated", lambda _r: self._delete())
            remove_group.add(remove)
            page.add(remove_group)
        toolbar.set_content(page)
        self._validate()

    def present(self) -> None:
        self.dialog.present(self._window)
        self._name.grab_focus()

    def _link(self) -> QuickLink:
        return QuickLink(
            name=self._name.get_text().strip(),
            url=self._url.get_text().strip(),
            alias=self._alias.get_text().strip(),
            icon=self._icon.get_text().strip(),
            fallback=self._fallback.get_active(),
            hotkey=self._hotkey.hotkey,
        )

    def _validate(self) -> bool:
        link = self._link()
        problems = {
            self._name: not link.name and "A name is required.",
            self._url: ("://" not in link.url and not link.url.startswith("mailto:"))
            and "The URL needs to start with https:// (or another scheme).",
            self._alias: any(ch.isspace() for ch in link.alias)
            and "The alias can't contain spaces.",
        }
        for row, problem in problems.items():
            (row.add_css_class if problem else row.remove_css_class)("error")
        is_search = "{query}" in link.url
        self._fallback.set_sensitive(is_search)
        if not is_search and self._fallback.get_active():
            self._fallback.set_active(False)
        message = next((p for p in problems.values() if p), None)
        _show_error(self._error, message)
        return message is None

    def _save(self) -> None:
        if not self._validate():
            return
        link = self._link()
        error = self._window.save(lambda w: w.save_quicklink(self._original, link))
        if error:
            _show_error(self._error, error)
        else:
            self.dialog.close()

    def _delete(self) -> None:
        if not self._window.save(lambda w: w.delete_quicklink(self._original)):
            self.dialog.close()


# --- snippets ------------------------------------------------------------------------

PLACEHOLDER_HELP = (
    ("{date}", "today, like 2026-09-24"),
    ("{date:%d %B %Y}", "any date or time format (strftime)"),
    ("{clipboard}", "what you copied last"),
    ("{cursor}", "where the cursor ends up"),
)


class SnippetDialog:
    """Add or edit one snippet: name, body, trigger (espanso), alias and hotkey."""

    def __init__(self, window: SettingsWindow, name: str | None, new_name: str = "") -> None:
        self._window = window
        self._original = name
        snippet = next(
            (s for s in window.config.snippets if name and s.name.casefold() == name.casefold()),
            Snippet(name=new_name, body=""),
        )
        self.dialog, toolbar, header = _dialog("Edit Snippet" if name else "New Snippet", 560)
        self.dialog.set_content_height(700)
        _save_cancel(self.dialog, header, self._save)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        self._name = Adw.EntryRow(title="Name", text=snippet.name)
        self._trigger = Adw.EntryRow(
            title="Trigger (typed anywhere, e.g. ;sig)", text=snippet.trigger
        )
        self._alias = Adw.EntryRow(title="Alias (exact search in the launcher)", text=snippet.alias)
        for row in (self._name, self._trigger, self._alias):
            row.connect("changed", lambda _e: self._validate())
            group.add(row)
        self._hotkey = HotkeyRow(
            window,
            "Hotkey",
            f"snippet '{name}'" if name else "snippet (new)",
            snippet.hotkey,
            lambda _k: self._validate(),
            subtitle="Pastes the snippet into the window you are in",
        )
        group.add(self._hotkey)
        page.add(group)

        body_group = Adw.PreferencesGroup(title="Text")
        self._body = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            monospace=True,
            top_margin=10,
            bottom_margin=10,
            left_margin=10,
            right_margin=10,
            accepts_tab=False,
        )
        self._body.get_buffer().set_text(snippet.body)
        self._body.get_buffer().connect("changed", lambda _b: self._validate())
        scroller = Gtk.ScrolledWindow(
            child=self._body, min_content_height=180, max_content_height=360,
            propagate_natural_height=True,
        )  # fmt: skip
        scroller.add_css_class("card")
        body_group.add(scroller)
        help_box = Gtk.Box(spacing=6, margin_top=8)
        for text, tip in PLACEHOLDER_HELP:
            button = Gtk.Button(label=text, tooltip_text=f"Insert {text}: {tip}")
            button.add_css_class("caption")
            button.connect("clicked", lambda _b, t=text: self._insert(t))
            help_box.append(button)
        body_group.add(help_box)
        self._error = _error_label()
        body_group.add(self._error)
        page.add(body_group)

        if name:
            remove_group = Adw.PreferencesGroup()
            remove = Adw.ButtonRow(title="Delete Snippet")
            remove.add_css_class("destructive-action")
            remove.connect("activated", lambda _r: self._delete())
            remove_group.add(remove)
            page.add(remove_group)
        toolbar.set_content(page)
        self._validate()

    def present(self) -> None:
        self.dialog.present(self._window)
        (self._body if self._name.get_text() else self._name).grab_focus()

    def _insert(self, text: str) -> None:
        buffer = self._body.get_buffer()
        buffer.insert_at_cursor(text)
        self._body.grab_focus()

    def _snippet(self) -> Snippet:
        buffer = self._body.get_buffer()
        return Snippet(
            name=self._name.get_text().strip(),
            body=buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False),
            trigger=self._trigger.get_text().strip(),
            alias=self._alias.get_text().strip(),
            hotkey=self._hotkey.hotkey,
        )

    def _validate(self) -> bool:
        snippet = self._snippet()
        problems = {
            self._name: not snippet.name and "A name is required.",
            self._trigger: (
                any(ch.isspace() for ch in snippet.trigger) and "The trigger can't contain spaces."
            )
            or (len(snippet.trigger) == 1 and "The trigger needs at least 2 characters."),
            self._alias: any(ch.isspace() for ch in snippet.alias)
            and "The alias can't contain spaces.",
        }
        for row, problem in problems.items():
            (row.add_css_class if problem else row.remove_css_class)("error")
        message = next((p for p in problems.values() if p), None)
        if message is None and not snippet.body:
            message = "The text is empty."
        _show_error(self._error, message)
        return message is None

    def _save(self) -> None:
        if not self._validate():
            return
        snippet = self._snippet()
        error = self._window.save(lambda w: w.save_snippet(self._original, snippet))
        if error:
            _show_error(self._error, error)
        else:
            self.dialog.close()

    def _delete(self) -> None:
        if not self._window.save(lambda w: w.delete_snippet(self._original)):
            self.dialog.close()
