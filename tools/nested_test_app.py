"""Tiny GTK window used by the nested-shell tests: logs key presses and entry text.

Every line it prints is JSON so the driver can parse it:
    {"event": "ready"} / {"event": "key", "keyval": "v", "ctrl": true, ...} /
    {"event": "text", "text": "..."} / {"event": "copied"}

It also takes commands on stdin, one per line:
    copy <text>          put text on the clipboard (as a real app would)
    copy-image <path>    put a PNG file on the clipboard
    clear                empty the entry
"""

import json
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402


def emit(**data):
    print(json.dumps(data), flush=True)


def on_activate(app):
    window = Gtk.ApplicationWindow(application=app, title="nested-test-app")
    entry = Gtk.Entry()
    entry.connect("changed", lambda e: emit(event="text", text=e.get_text()))
    keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)

    def on_key(_ctrl, keyval, _code, state):
        emit(
            event="key",
            keyval=Gdk.keyval_name(keyval),
            ctrl=bool(state & Gdk.ModifierType.CONTROL_MASK),
            shift=bool(state & Gdk.ModifierType.SHIFT_MASK),
        )
        return False

    keys.connect("key-pressed", on_key)
    window.add_controller(keys)
    window.set_child(entry)

    def on_stdin(channel, _condition):
        line = channel.readline()
        if not line:
            return GLib.SOURCE_REMOVE
        command, _, arg = line.rstrip("\n").partition(" ")
        clipboard = window.get_clipboard()
        if command == "copy":
            clipboard.set(arg)
            emit(event="copied")
        elif command == "copy-image":
            # A typed GValue: plain clipboard.set(texture) would offer it as a string.
            texture = Gdk.Texture.new_from_filename(arg)
            value = GObject.Value(Gdk.Texture, texture)
            clipboard.set_content(Gdk.ContentProvider.new_for_value(value))
            emit(event="copied")
        elif command == "clear":
            entry.set_text("")
        return GLib.SOURCE_CONTINUE

    GLib.io_add_watch(GLib.IOChannel.unix_new(0), GLib.PRIORITY_DEFAULT, GLib.IO_IN, on_stdin)
    window.connect("notify::is-active", lambda w, _p: w.is_active() and emit(event="ready"))
    window.present()


app = Gtk.Application(application_id="io.github.jinwei.LauncherNestedTest")
app.connect("activate", on_activate)
sys.exit(app.run([]))
