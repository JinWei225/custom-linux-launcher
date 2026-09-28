"""The Notes app as Super+Shift+N runs it, in a headless nested GNOME Shell.

`launcher --notes` starts Notes; while it runs, the same command (sent over D-Bus
without loading GTK) hides it when it is the focused window and brings it back
otherwise. Closing hides it too, so it reappears at once; Ctrl+Q quits. Saving happens
before every hide. Uses the test-only nested-input extension for keys and window
lookups. Run through:

    tools/nested-shell.sh .venv/bin/python tools/nested_notes_app_test.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gio, GLib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = str(ROOT / ".venv/bin/launcher")
NOTES = Path(os.environ["XDG_DATA_HOME"]) / "Notes"
CONFIG = Path(os.environ["XDG_CONFIG_HOME"]) / "launcher" / "config.toml"
TITLE = "Week 1 – Notes"
results: list[tuple[str, bool]] = []
bus = Gio.bus_get_sync(Gio.BusType.SESSION)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    sys.stdout.flush()


def call(method: str, args: GLib.Variant | None = None):
    return bus.call_sync(
        "org.gnome.Shell", "/io/github/jinwei/NestedInput", "io.github.jinwei.NestedInput",
        method, args, None, Gio.DBusCallFlags.NONE, 3000, None,
    ).unpack()  # fmt: skip


def shown(title: str = TITLE) -> bool:
    return call("WindowFrame", GLib.Variant("(s)", (title,)))[0][0] != -1


def focused() -> str:
    return call("FocusedTitle")[0]


def key(keyval: int, pressed: bool | None = None) -> None:
    for state in (True, False) if pressed is None else (pressed,):
        call("Key", GLib.Variant("(ub)", (keyval, state)))
    time.sleep(0.03)


def ctrl(keyval: int) -> None:
    key(Gdk.KEY_Control_L, True)
    key(keyval)
    key(Gdk.KEY_Control_L, False)


def wait_for(predicate, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def notes_cli(*args: str) -> float:
    """Run `launcher --notes …` to completion (a running Notes); returns seconds."""
    start = time.monotonic()
    subprocess.run([LAUNCHER, "--notes", *args], check=False, timeout=10)
    return time.monotonic() - start


def notes_running() -> bool:
    return bool(
        bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", ("io.github.jinwei.Launcher.Notes",)),
            None, Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]
    )  # fmt: skip


def main() -> int:
    NOTES.mkdir(parents=True, exist_ok=True)
    (NOTES / "Week 1.md").write_text("# Week 1\n")
    (NOTES / "Week 2.md").write_text("# Week 2\n")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(f'[notes]\nfolder = "{NOTES}"\n')
    # Start the desktop portal first: in a fresh session the first GTK app waits seconds
    # for it, which would count against Notes' own start.
    subprocess.run([sys.executable, "-c", "import gi; gi.require_version('Adw', '1'); "
                    "from gi.repository import Adw; Adw.init()"], check=False)  # fmt: skip

    start = time.monotonic()
    first = subprocess.Popen([LAUNCHER, "--notes", "--open", "Week 1.md"])
    appeared = wait_for(shown, 10)
    check("launcher --notes starts Notes", appeared, f"{time.monotonic() - start:.2f}s")
    check("…focused", wait_for(lambda: focused() == TITLE), focused())

    # Type, then hide with the shortcut's command: saved first, and it hides.
    ctrl(Gdk.KEY_End)  # the empty line under the title (typing in it would rename the note)
    for ch in "notes":
        key(Gdk.unicode_to_keyval(ord(ch)))
    seconds = notes_cli()
    check("the shortcut hides focused Notes", wait_for(lambda: not shown()), focused())
    check("…fast, without loading GTK", seconds < 0.25, f"{seconds:.2f}s")
    saved = (NOTES / "Week 1.md").read_text()
    check("…after saving", saved == "# Week 1\nnotes", repr(saved))
    check("…and keeps running", notes_running())

    start = time.monotonic()
    notes_cli()
    back = wait_for(shown, 5)
    check("the shortcut brings it back", back and wait_for(lambda: focused() == TITLE))
    check("…at once", time.monotonic() - start < 0.6, f"{time.monotonic() - start:.2f}s")

    # Another app is focused: the shortcut brings Notes to the front instead of hiding.
    other = subprocess.Popen(
        [sys.executable, str(ROOT / "tools/nested_test_app.py")],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )  # fmt: skip
    wait_for(lambda: focused() == "nested-test-app", 10)
    check("(another app took focus)", focused() == "nested-test-app", focused())
    notes_cli()
    check("the shortcut focuses Notes when it is behind another window",
          wait_for(lambda: focused() == TITLE) and shown(), focused())  # fmt: skip
    other.terminate()

    # Closing (Ctrl+W) hides too; --open brings it back with that note.
    ctrl(Gdk.KEY_w)
    check("Ctrl+W hides Notes", wait_for(lambda: not shown()))
    check("…which keeps running", notes_running())
    notes_cli("--open", "Week 2.md")
    check("--open shows the note", wait_for(lambda: shown("Week 2 – Notes")), focused())

    # Ctrl+Q quits for real.
    ctrl(Gdk.KEY_q)
    check("Ctrl+Q quits Notes", wait_for(lambda: not notes_running(), 5))
    check("…and the first process exits", wait_for(lambda: first.poll() is not None, 5))
    if first.poll() is None:
        first.kill()

    passed = sum(ok for _, ok in results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
