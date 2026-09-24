"""The launcher window: search entry, result list and keyboard handling."""

from __future__ import annotations

import logging
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .config import Config  # noqa: E402
from .engine import Engine  # noqa: E402
from .providers.base import Result  # noqa: E402

log = logging.getLogger(__name__)

ROW_HEIGHT = 56  # must match .results row in style.css (min-height + padding + margin)
FOCUS_CHECK_MS = 400
# Some apps flash a window that takes focus for a few ms (espanso's worker does on
# restart). Only hide if focus has not come back to us after this long.
FOCUS_LOSS_GRACE_MS = 150

PLACEHOLDERS = {
    "all": "Search apps, links and the web…",
    "apps": "Search apps…",
    "files": "Search files…",
    "clipboard": "Search clipboard history…",
    "snippets": "Search snippets…",
}

_NUMBER_KEYS = {getattr(Gdk, f"KEY_{n}"): n for n in range(1, 10)}
_ENTER_KEYS = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter)


class ResultRow(Gtk.ListBoxRow):
    def __init__(self, result: Result, index: int) -> None:
        super().__init__()
        self.result = result

        icon = Gtk.Image(pixel_size=32)
        icon.add_css_class("result-icon")
        gicon = _gicon(result.icon)
        if gicon is not None:
            icon.set_from_gicon(gicon)
        else:
            icon.set_from_icon_name("system-run-symbolic")

        title = Gtk.Label(label=result.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("result-title")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        text.append(title)
        if result.subtitle:
            subtitle = Gtk.Label(label=result.subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            subtitle.add_css_class("dim-label")
            subtitle.add_css_class("caption")
            text.append(subtitle)

        box = Gtk.Box(spacing=12)
        box.append(icon)
        box.append(text)
        if index < 9:
            hint = Gtk.Label(label=f"Ctrl+{index + 1}")
            hint.add_css_class("dim-label")
            hint.add_css_class("caption")
            box.append(hint)
        self.set_child(box)


class LauncherWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, engine: Engine, config: Config) -> None:
        super().__init__(application=app, title="Launcher")
        self._app = app
        self._engine = engine
        self._mode = "all"
        self._preedit = False
        self._shown_at = 0.0
        self.config = config

        self.set_resizable(False)
        self.set_size_request(-1, -1)  # drop libadwaita's 360x200 minimum; size to content
        self.set_hide_on_close(True)
        self.add_css_class("launcher")

        # Adw.Banner does not wrap its title, so a long error would widen the window.
        self._problem_label = Gtk.Label(
            xalign=0,
            hexpand=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            max_width_chars=1,  # wrap to the allocated width instead of widening the window
        )
        open_config = Gtk.Button(label="Open Config", valign=Gtk.Align.CENTER)
        open_config.connect("clicked", lambda _b: app.activate_action("open-config", None))
        problem_box = Gtk.Box(spacing=12)
        problem_box.add_css_class("problem-banner")
        problem_box.append(self._problem_label)
        problem_box.append(open_config)
        self._banner = Gtk.Revealer(child=problem_box)

        self._entry = Gtk.SearchEntry(hexpand=True)
        self._entry.add_css_class("launcher-entry")
        self._entry.connect("changed", lambda _e: self._refresh())
        # Track IME composition (Pinyin, Hangul) so Enter/arrows/Esc go to the input method.
        self._entry.get_delegate().connect("preedit-changed", self._on_preedit_changed)

        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self._list.add_css_class("results")
        self._list.connect("row-activated", lambda _l, row: self._run(row.result, alt=False))

        self._scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            child=self._list,
            visible=False,
        )

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._content.append(self._banner)
        self._content.append(self._entry)
        self._content.append(self._scroller)
        self.set_content(self._content)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        self.connect("notify::is-active", self._on_active_changed)
        self._surface: Gdk.Surface | None = None
        self._window_focused = False
        self._hide_source = 0
        self.connect("realize", self._on_realize)

        self.apply_config(config)

    # --- public API used by the app ---------------------------------------------------

    def show_mode(self, mode: str) -> None:
        self._shown_at = time.monotonic()
        self._mode = mode
        self._entry.set_placeholder_text(PLACEHOLDERS.get(mode, ""))
        if self._entry.get_text():
            self._entry.set_text("")  # triggers _refresh via "changed"
        else:
            self._refresh()
        self.present()
        self._entry.grab_focus()
        GLib.timeout_add(FOCUS_CHECK_MS, self._check_focus)

    def toggle_mode(self, mode: str) -> None:
        if self.get_visible() and mode == self._mode:
            self.hide_launcher()
        else:
            self.show_mode(mode)

    def hide_launcher(self) -> None:
        self._cancel_pending_hide()
        self.set_visible(False)

    def refresh_if_visible(self) -> None:
        """Re-render results (e.g. a website icon arrived) without moving the selection."""
        if not self.get_visible():
            return
        row = self._list.get_selected_row()
        index = row.get_index() if row is not None else 0
        self._refresh()
        target = self._list.get_row_at_index(index)
        if target is not None:
            self._list.select_row(target)

    def set_query(self, text: str) -> None:
        self._entry.set_text(text)
        self._entry.set_position(-1)

    def apply_config(self, config: Config) -> None:
        self.config = config
        self._content.set_size_request(config.ui.width, -1)
        self._scroller.set_max_content_height(config.ui.max_results * ROW_HEIGHT)
        if self.get_visible():
            self._refresh()

    def set_problems(self, problems: list[str]) -> None:
        self._problem_label.set_text("Config: " + "; ".join(problems) if problems else "")
        self._banner.set_reveal_child(bool(problems))

    def save_snapshot(self, path: str) -> None:
        """Debug helper: render the window to a PNG (GNOME blocks normal screenshots)."""
        paintable = Gtk.WidgetPaintable.new(self._content)
        snapshot = Gtk.Snapshot()
        width, height = self._content.get_width(), self._content.get_height()
        # Paint the window background first; the content box itself is transparent.
        snapshot.append_color(self._background_rgba(), _rect(width, height))
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node is None:
            log.warning("snapshot: nothing to render (is the window visible?)")
            return
        self.get_renderer().render_texture(node, None).save_to_png(path)
        log.info("snapshot saved to %s (%dx%d)", path, width, height)

    # --- results ---------------------------------------------------------------------

    def _refresh(self) -> None:
        results = self._engine.query(self._entry.get_text(), self._mode, self.config.ui.max_results)
        self._list.remove_all()
        for i, result in enumerate(results):
            self._list.append(ResultRow(result, i))
        first = self._list.get_row_at_index(0)
        if first is not None:
            self._list.select_row(first)
        self._scroller.set_visible(bool(results))

    def _move_selection(self, delta: int) -> None:
        row = self._list.get_selected_row()
        index = (row.get_index() if row else -1) + delta
        target = self._list.get_row_at_index(max(index, 0))
        if target is not None:
            self._list.select_row(target)
            self._scroll_to(target)

    def _scroll_to(self, row: Gtk.ListBoxRow) -> None:
        ok, bounds = row.compute_bounds(self._list)
        if not ok:
            return
        adj = self._scroller.get_vadjustment()
        top, bottom = bounds.get_y(), bounds.get_y() + bounds.get_height()
        if top < adj.get_value():
            adj.set_value(top)
        elif bottom > adj.get_value() + adj.get_page_size():
            adj.set_value(bottom - adj.get_page_size())

    def run_selected(self, alt: bool = False) -> None:
        row = self._list.get_selected_row()
        if row is not None:
            self._run(row.result, alt)

    def _run_index(self, index: int, alt: bool) -> None:
        row = self._list.get_row_at_index(index)
        if row is not None:
            self._run(row.result, alt)

    def _run(self, result: Result, alt: bool) -> None:
        action = result.alt_action if alt and result.alt_action else result.action
        if action is None:
            return
        # Run while still focused: the activation token handed to launched apps (and a
        # clipboard write) is only honoured from the focused window. Then hide.
        try:
            action()
        except Exception as e:
            log.exception("action for %s failed", result.id)
            self._app.notify_error(f"“{result.title}” failed", str(e))
        else:
            self._engine.record(result)
        finally:
            self.hide_launcher()

    def _edit_selected(self) -> None:
        """Ctrl+E: open Launcher Settings at the selected app or quicklink."""
        row = self._list.get_selected_row()
        if row is None:
            return
        edit = edit_target(row.result.id)
        if edit is None:
            return
        self._app.open_settings(edit)
        self.hide_launcher()

    def _complete(self) -> None:
        row = self._list.get_selected_row()
        if row is not None and row.result.completion:
            self.set_query(row.result.completion)

    # --- events ----------------------------------------------------------------------

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        if self._preedit:
            return False  # the input method is composing; let it have the key
        mods = state & Gtk.accelerator_get_default_mod_mask()
        ctrl = mods == Gdk.ModifierType.CONTROL_MASK
        alt = mods == Gdk.ModifierType.ALT_MASK

        if keyval == Gdk.KEY_Escape:
            self.hide_launcher()
        elif keyval == Gdk.KEY_Down or (ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_j)):
            self._move_selection(1)
        elif keyval == Gdk.KEY_Up or (ctrl and keyval in (Gdk.KEY_p, Gdk.KEY_k)):
            self._move_selection(-1)
        elif keyval in _ENTER_KEYS and (not mods or alt):
            self.run_selected(alt=alt)
        elif ctrl and keyval in _NUMBER_KEYS:
            self._run_index(_NUMBER_KEYS[keyval] - 1, alt=False)
        elif ctrl and keyval == Gdk.KEY_e:
            self._edit_selected()
        elif keyval == Gdk.KEY_Tab and not mods:
            self._complete()  # always consume Tab so focus never leaves the search box
        else:
            return False
        return True

    def _on_preedit_changed(self, _text, preedit: str) -> None:
        self._preedit = bool(preedit)

    def _on_active_changed(self, *_args) -> None:
        # is-active follows *keyboard* focus, which GNOME Shell also takes away while its
        # own popups are open (Super+Space input switcher, Alt+Tab, polkit dialogs...).
        # So it is only used for logging; hiding is driven by _on_surface_state.
        if self.is_active():
            elapsed_ms = (time.monotonic() - self._shown_at) * 1000
            log.debug("keyboard focus in %.0f ms after request", elapsed_ms)
        else:
            log.debug("keyboard focus out")

    def _on_realize(self, *_args) -> None:
        surface = self.get_surface()
        if surface is self._surface:
            return
        self._surface = surface
        surface.connect("notify::state", self._on_surface_state)

    def _on_surface_state(self, surface: Gdk.Toplevel, _pspec) -> None:
        # The compositor's "this window is the focused window" state. Unlike keyboard focus
        # it stays set while a Shell popup is open, and only drops when another window
        # really becomes the focused one.
        focused = bool(surface.get_state() & Gdk.ToplevelState.FOCUSED)
        if focused == self._window_focused:
            return
        self._window_focused = focused
        log.debug("window %s", "focused" if focused else "lost focus to another window")
        if focused:
            self._cancel_pending_hide()
        elif self.get_visible() and self.config.ui.hide_on_focus_loss and not self._hide_source:
            self._hide_source = GLib.timeout_add(FOCUS_LOSS_GRACE_MS, self._hide_if_still_unfocused)

    def _hide_if_still_unfocused(self) -> bool:
        self._hide_source = 0
        if not self._window_focused:
            log.debug("hiding: another window kept focus for %d ms", FOCUS_LOSS_GRACE_MS)
            self.hide_launcher()
        return GLib.SOURCE_REMOVE

    def _cancel_pending_hide(self) -> None:
        if self._hide_source:
            GLib.source_remove(self._hide_source)
            self._hide_source = 0

    def _check_focus(self) -> bool:
        if self.get_visible() and not self.is_active():
            log.warning(
                "window is visible but did not get focus within %d ms "
                "(GNOME focus-stealing prevention; was an activation token passed?)",
                FOCUS_CHECK_MS,
            )
        return GLib.SOURCE_REMOVE

    def _background_rgba(self) -> Gdk.RGBA:
        rgba = Gdk.RGBA()
        dark = Adw.StyleManager.get_default().get_dark()
        rgba.parse("#222226" if dark else "#ffffff")
        return rgba


def edit_target(result_id: str) -> str | None:
    """Which settings item Ctrl+E opens for a result, if any."""
    kind, _, key = result_id.partition(":")
    if kind == "app":
        return result_id
    if kind in ("quicklink", "websearch"):
        return f"quicklink:{key}"
    return None


def _gicon(spec: str | None) -> Gio.Icon | None:
    if not spec:
        return None
    try:
        return Gio.Icon.new_for_string(spec)
    except GLib.Error:
        return None


def _rect(width: int, height: int):
    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene

    return Graphene.Rect().init(0, 0, width, height)
