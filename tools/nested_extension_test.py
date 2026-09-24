"""Checks the Launcher Helper extension inside a headless nested GNOME Shell.

Run through tools/nested-shell.sh, which provides a private session bus, settings and
Wayland display. Acts as the launcher (owns its bus name) and exercises every method.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

DEST = "org.gnome.Shell"
PATH = "/io/github/jinwei/LauncherHelper"
IFACE = "io.github.jinwei.LauncherHelper"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082"
)

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
results: list[tuple[str, bool, str]] = []
signals: list[tuple] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))


def pump(seconds: float) -> None:
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.01)


def call(method: str, args: GLib.Variant | None = None, connection=None, timeout=5000):
    return (connection or bus).call_sync(
        DEST, PATH, IFACE, method, args, None, Gio.DBusCallFlags.NONE, timeout, None
    )


def main() -> int:
    # 1. The extension is loaded and answers.
    for _ in range(100):
        try:
            version = call("GetVersion").unpack()[0]
            break
        except GLib.Error:
            pump(0.1)
    else:
        check("extension answers GetVersion", False, "no answer after 10 s")
        return 1
    check("extension answers GetVersion", version == 1, str(version))

    # 2. Before we own the launcher's name, protected methods are refused.
    try:
        call("GetFocusedWindow")
        check("strangers are refused", False, "call succeeded")
    except GLib.Error as e:
        check("strangers are refused", "AccessDenied" in e.message, e.message)

    # Become "the launcher".
    owned = []
    Gio.bus_own_name_on_connection(
        bus, "io.github.jinwei.Launcher", Gio.BusNameOwnerFlags.NONE,
        lambda *_: owned.append(True), None,
    )  # fmt: skip
    pump(1.0)
    check("own the launcher name", bool(owned))

    bus.signal_subscribe(
        DEST, IFACE, "ClipboardChanged", PATH, None, Gio.DBusSignalFlags.NONE,
        lambda *args: signals.append(args[5].unpack()),
    )  # fmt: skip

    # 3. Text round trip + change signal.
    text = "hello from the nested test ✓"
    call("SetClipboard", GLib.Variant("(say)", ("text/plain;charset=utf-8", text.encode())))
    pump(0.5)
    got = bytes(
        call("GetClipboard", GLib.Variant("(s)", ("text/plain;charset=utf-8",))).unpack()[0]
    )
    check("text round trip", got.decode() == text, repr(got[:60]))
    check(
        "ClipboardChanged fired with (mimetypes, wm_class, app_id)",
        any(len(s) == 3 and "text/plain;charset=utf-8" in s[0] for s in signals),
        repr(signals[-1:]),
    )

    # 4. Image round trip (and big payloads survive).
    call("SetClipboard", GLib.Variant("(say)", ("image/png", PNG)))
    pump(0.3)
    got = bytes(call("GetClipboard", GLib.Variant("(s)", ("image/png",))).unpack()[0])
    check("image round trip", got == PNG, f"{len(got)} bytes")
    big = os.urandom(8 * 1024 * 1024)
    call("SetClipboard", GLib.Variant("(say)", ("application/x-test", big)), timeout=20000)
    pump(0.3)
    got = bytes(
        call("GetClipboard", GLib.Variant("(s)", ("application/x-test",)), timeout=20000).unpack()[
            0
        ]
    )
    check("8 MB round trip", got == big, f"{len(got)} bytes")

    # 5. Paste into a real window.
    env = dict(os.environ)
    app = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "nested_test_app.py")],
        env=env, stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    os.set_blocking(app.stdout.fileno(), False)
    events: list[dict] = []

    def read_events(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            line = app.stdout.readline()
            if line:
                events.append(json.loads(line))
            else:
                pump(0.02)

    read_events(4)
    check("test window got focus", any(e["event"] == "ready" for e in events), repr(events))
    window_id, wm_class, app_id = call("GetFocusedWindow").unpack()
    check("GetFocusedWindow sees the test window", window_id != 0, f"{wm_class} {app_id}")

    call("SetClipboard", GLib.Variant("(say)", ("text/plain;charset=utf-8", b"pasted-by-helper")))
    pump(0.3)
    pasted = call("Paste", GLib.Variant("(ub)", (window_id, False))).unpack()[0]
    read_events(1.5)
    texts = [e["text"] for e in events if e["event"] == "text"]
    check("Paste reported success", pasted)
    check("Ctrl+V pasted into the entry", "pasted-by-helper" in texts, repr(texts[-3:]))

    events.clear()
    call("Paste", GLib.Variant("(ub)", (window_id, True)))
    read_events(1.5)
    keys = [e for e in events if e["event"] == "key" and e["keyval"] in ("v", "V")]
    check(
        "with_shift sends Ctrl+Shift+V",
        any(k["ctrl"] and k["shift"] for k in keys),
        repr(keys),
    )

    missing = call("Paste", GLib.Variant("(ub)", (987654, False))).unpack()[0]
    check("Paste to a missing window returns false", missing is False)

    app.terminate()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
