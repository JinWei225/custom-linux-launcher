// Test-only: real pointer and keyboard input inside the nested GNOME Shell used by
// tools/nested-shell.sh, so tests can click, hover and type like a person (GTK has no
// way to fake input from inside an app). tools/nested-shell.sh installs it; it is never
// part of a real session.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {getInputSourceManager} from 'resource:///org/gnome/shell/ui/status/keyboard.js';

const IFACE = `
<node>
  <interface name="io.github.jinwei.NestedInput">
    <method name="Move"><arg type="d" name="x" direction="in"/><arg type="d" name="y" direction="in"/></method>
    <method name="Button"><arg type="u" name="button" direction="in"/><arg type="b" name="pressed" direction="in"/></method>
    <method name="Key"><arg type="u" name="keyval" direction="in"/><arg type="b" name="pressed" direction="in"/></method>
    <method name="SetInputSource">
      <arg type="u" name="index" direction="in"/>
      <arg type="s" name="id" direction="out"/>
    </method>
    <method name="WindowFrame">
      <arg type="s" name="title" direction="in"/>
      <arg type="ai" name="rect" direction="out"/>
    </method>
    <method name="FocusedTitle"><arg type="s" name="title" direction="out"/></method>
  </interface>
</node>`;

export default class NestedInput {
    enable() {
        const seat = Clutter.get_default_backend().get_default_seat();
        this._pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        this._keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/io/github/jinwei/NestedInput');
    }

    disable() {
        this._dbus?.unexport();
        this._dbus = null;
        this._pointer = null;
        this._keyboard = null;
    }

    Move(x, y) {
        this._pointer.notify_absolute_motion(GLib.get_monotonic_time(), x, y);
    }

    Button(button, pressed) {
        const state = pressed ? Clutter.ButtonState.PRESSED : Clutter.ButtonState.RELEASED;
        this._pointer.notify_button(GLib.get_monotonic_time(), button, state);
    }

    Key(keyval, pressed) {
        const state = pressed ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED;
        this._keyboard.notify_keyval(GLib.get_monotonic_time(), keyval, state);
    }

    SetInputSource(index) {
        // Like Super+Space: switch to the index-th input source (e.g. an IBus engine).
        const source = getInputSourceManager().inputSources[index];
        if (!source)
            return '';
        source.activate(true);
        return source.id;
    }

    WindowFrame(title) {
        const actor = global.get_window_actors().find(a => a.meta_window.get_title() === title);
        if (!actor)
            return [-1, -1, 0, 0];
        const r = actor.meta_window.get_frame_rect();
        return [r.x, r.y, r.width, r.height];
    }

    FocusedTitle() {
        return global.display.focus_window?.get_title() ?? '';
    }
}
