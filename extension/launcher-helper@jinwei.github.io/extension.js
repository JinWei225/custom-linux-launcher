// Launcher Helper: the few things GNOME on Wayland only lets the Shell do.
//
//  * watch the clipboard (signal ClipboardChanged: content types + source app only)
//  * read / write clipboard content (GetClipboard / SetClipboard)
//  * remember the focused window and paste into it (GetFocusedWindow / Paste)
//
// Everything else lives in the launcher (Python). Keep this file small: every change
// needs a log out / log in on Wayland.
//
// Only the process owning the launcher's bus name may call the methods, so other apps
// cannot use this extension to read the clipboard or type keystrokes.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const VERSION = 1;
const LAUNCHER_BUS_NAME = 'io.github.jinwei.Launcher';
const OBJECT_PATH = '/io/github/jinwei/LauncherHelper';
const ERROR_PREFIX = 'io.github.jinwei.LauncherHelper.Error';
const FOCUS_TIMEOUT_MS = 700;

const IFACE = `
<node>
  <interface name="io.github.jinwei.LauncherHelper">
    <method name="GetVersion">
      <arg type="u" name="version" direction="out"/>
    </method>
    <method name="GetFocusedWindow">
      <arg type="u" name="window_id" direction="out"/>
      <arg type="s" name="wm_class" direction="out"/>
      <arg type="s" name="app_id" direction="out"/>
    </method>
    <method name="GetClipboard">
      <arg type="s" name="mimetype" direction="in"/>
      <arg type="ay" name="data" direction="out"/>
    </method>
    <method name="SetClipboard">
      <arg type="s" name="mimetype" direction="in"/>
      <arg type="ay" name="data" direction="in"/>
    </method>
    <method name="Paste">
      <arg type="u" name="window_id" direction="in"/>
      <arg type="b" name="with_shift" direction="in"/>
      <arg type="b" name="pasted" direction="out"/>
    </method>
    <signal name="ClipboardChanged">
      <arg type="as" name="mimetypes"/>
      <arg type="s" name="wm_class"/>
      <arg type="s" name="app_id"/>
    </signal>
  </interface>
</node>`;

function describeWindow(win) {
    if (!win)
        return [0, '', ''];
    const app = Shell.WindowTracker.get_default().get_window_app(win);
    return [win.get_stable_sequence(), win.get_wm_class() ?? '', app?.get_id() ?? ''];
}

class Helper {
    constructor() {
        this._launcherOwner = null;
        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SESSION, LAUNCHER_BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            (_conn, _name, owner) => {
                this._launcherOwner = owner;
            },
            () => {
                this._launcherOwner = null;
            });

        this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbusImpl.export(Gio.DBus.session, OBJECT_PATH);

        this._selection = global.display.get_selection();
        this._ownerChangedId = this._selection.connect('owner-changed',
            (_sel, type) => this._onOwnerChanged(type));
        this._virtualKeyboard = global.stage.context.get_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    }

    destroy() {
        this._selection.disconnect(this._ownerChangedId);
        this._dbusImpl.unexport();
        Gio.bus_unwatch_name(this._watchId);
        this._virtualKeyboard = null;
    }

    _onOwnerChanged(type) {
        if (type !== Meta.SelectionType.SELECTION_CLIPBOARD)
            return;
        const mimetypes = this._selection.get_mimetypes(type);
        if (mimetypes.length === 0)
            return; // the owner went away; nothing new was copied
        const [, wmClass, appId] = describeWindow(global.display.get_focus_window());
        this._dbusImpl.emit_signal('ClipboardChanged',
            new GLib.Variant('(asss)', [mimetypes, wmClass, appId]));
    }

    _denied(invocation) {
        if (this._launcherOwner && invocation.get_sender() === this._launcherOwner)
            return false;
        invocation.return_dbus_error(`${ERROR_PREFIX}.AccessDenied`,
            'Only the launcher may use Launcher Helper');
        return true;
    }

    GetVersionAsync(_params, invocation) {
        // Not protected: lets the launcher check that the extension is active.
        invocation.return_value(new GLib.Variant('(u)', [VERSION]));
    }

    GetFocusedWindowAsync(_params, invocation) {
        if (this._denied(invocation))
            return;
        invocation.return_value(new GLib.Variant('(uss)',
            describeWindow(global.display.get_focus_window())));
    }

    GetClipboardAsync(params, invocation) {
        if (this._denied(invocation))
            return;
        const [mimetype] = params;
        St.Clipboard.get_default().get_content(St.ClipboardType.CLIPBOARD, mimetype,
            (_clipboard, bytes) => {
                const data = bytes ?? new GLib.Bytes(new Uint8Array(0));
                invocation.return_value(GLib.Variant.new_tuple([
                    GLib.Variant.new_from_bytes(new GLib.VariantType('ay'), data, true),
                ]));
            });
    }

    SetClipboardAsync(_params, invocation) {
        if (this._denied(invocation))
            return;
        // Read the raw parameters so large images are not unpacked into JS arrays.
        const parameters = invocation.get_parameters();
        const mimetype = parameters.get_child_value(0).get_string()[0];
        const data = parameters.get_child_value(1).get_data_as_bytes();
        St.Clipboard.get_default().set_content(St.ClipboardType.CLIPBOARD, mimetype, data);
        invocation.return_value(null);
    }

    PasteAsync(params, invocation) {
        if (this._denied(invocation))
            return;
        const [windowId, withShift] = params;
        const target = global.display.list_all_windows()
            .find(w => w.get_stable_sequence() === windowId);
        if (!target) {
            invocation.return_value(new GLib.Variant('(b)', [false]));
            return;
        }
        this._whenFocused(target, focused => {
            if (focused)
                this._sendPaste(withShift);
            invocation.return_value(new GLib.Variant('(b)', [focused]));
        });
    }

    _whenFocused(win, callback) {
        // The launcher has just hidden its window; wait for focus to come back to the
        // window it was opened from, and ask for it explicitly if it doesn't.
        if (global.display.get_focus_window() === win) {
            callback(true);
            return;
        }
        let done = false;
        const finish = focused => {
            if (done)
                return;
            done = true;
            global.display.disconnect(signalId);
            GLib.source_remove(timeoutId);
            // Give the app a moment to process the focus change before keys arrive.
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 30, () => {
                callback(focused);
                return GLib.SOURCE_REMOVE;
            });
        };
        const signalId = global.display.connect('notify::focus-window', () => {
            if (global.display.get_focus_window() === win)
                finish(true);
        });
        const timeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, FOCUS_TIMEOUT_MS, () => {
            finish(global.display.get_focus_window() === win);
            return GLib.SOURCE_REMOVE;
        });
        Main.activateWindow(win);
    }

    _sendPaste(withShift) {
        const keys = [Clutter.KEY_Control_L];
        if (withShift)
            keys.push(Clutter.KEY_Shift_L);
        keys.push(Clutter.KEY_v);
        const time = Clutter.get_current_event_time() * 1000;
        for (const key of keys)
            this._virtualKeyboard.notify_keyval(time, key, Clutter.KeyState.PRESSED);
        for (const key of keys.reverse())
            this._virtualKeyboard.notify_keyval(time, key, Clutter.KeyState.RELEASED);
    }
}

export default class LauncherHelperExtension extends Extension {
    enable() {
        this._helper = new Helper();
    }

    disable() {
        this._helper?.destroy();
        this._helper = null;
    }
}
