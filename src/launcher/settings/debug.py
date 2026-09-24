"""Developer aid: render every settings page and dialog to PNGs, then quit.

GNOME on Wayland blocks ordinary screenshots, so this paints the window itself.
Used as `launcher --settings --debug-snapshots DIR`.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

log = logging.getLogger(__name__)
STEP_MS = 700


def snapshot(widget: Gtk.Widget, path: str) -> None:
    width, height = widget.get_width(), widget.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snap, width, height)
    node = snap.to_node()
    if node is None:
        log.warning("nothing to render for %s", path)
        return
    texture = (
        widget.get_native()
        .get_renderer()
        .render_texture(node, Graphene.Rect().init(0, 0, width, height))
    )
    texture.save_to_png(path)
    log.info("saved %s (%dx%d)", path, width, height)


def render_all(window, directory: str) -> None:
    from .dialogs import AppDialog, QuicklinkDialog, ShortcutDialog

    steps = []
    for name in ("general", "shortcuts", "apps", "quicklinks", "files"):
        steps.append((lambda n=name: window._stack.set_visible_child_name(n), f"page-{name}"))
    first_link = window.config.quicklinks[0].name if window.config.quicklinks else None
    apps = window.app_catalog()
    dialogs = [
        (lambda: AppDialog(window, apps[0].id).present(), "dialog-app"),
        (lambda: QuicklinkDialog(window, first_link).present(), "dialog-quicklink"),
        (lambda: QuicklinkDialog(window, None).present(), "dialog-quicklink-new"),
        (
            lambda: ShortcutDialog(window, "Hotkey", "test", lambda k: None).present(),
            "dialog-shortcut",
        ),
    ]

    def close_dialog() -> None:
        dialog = window.get_visible_dialog()
        if dialog is not None:
            dialog.force_close()

    for open_dialog, name in dialogs:
        steps.append((lambda o=open_dialog: (close_dialog(), o()), name))

    def run(i: int = 0) -> bool:
        if i > 0:
            snapshot(window, f"{directory}/{steps[i - 1][1]}.png")
        if i == len(steps):
            window.get_application().quit()
            return GLib.SOURCE_REMOVE
        steps[i][0]()
        GLib.timeout_add(STEP_MS, run, i + 1)
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(STEP_MS, run)
