"""End-to-end test of the Notes window inside a headless nested GNOME Shell.

Drives the real NotesWindow in this process (pumping the GLib main loop by hand) and
checks what lands on disk: autosave after a pause, saving on focus loss, renaming on a
title edit, pins, reloading after another app's change, discarding an empty new note,
restoring the last note, and `launcher --notes` staying single-instance. Run through:

    tools/nested-shell.sh .venv/bin/python tools/nested_notes_test.py [snapshot dir]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from launcher import paths  # noqa: E402
from launcher.notes.window import AUTOSAVE_DELAY_MS, NotesWindow, state_file  # noqa: E402
from launcher.settings.debug import snapshot  # noqa: E402

SNAPSHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
NOTES = Path(os.environ["XDG_DATA_HOME"]) / "Notes"
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))


def pump(seconds: float) -> None:
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)


def wait_for(predicate, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(0.05)
        if predicate():
            return True
    return False


def type_text(window: NotesWindow, text: str) -> None:
    """Type at the end of the note (opening a note puts the cursor at the start)."""
    window.buffer.place_cursor(window.buffer.get_end_iter())
    window.buffer.insert_at_cursor(text)
    pump(0.05)


def sidebar_rows(window: NotesWindow) -> list[tuple[str, str]]:
    rows, i = [], 0
    while (row := window.sidebar.list.get_row_at_index(i)) is not None:
        rows.append((row.kind, row.label))
        i += 1
    return rows


def open_menu(window: NotesWindow, kind: str, label: str) -> Gtk.PopoverMenu | None:
    """Right-click the first sidebar row of this kind and label; returns its menu."""
    pump(0.2)  # let a just-rebuilt sidebar lay out its rows first
    i = 0
    while (row := window.sidebar.list.get_row_at_index(i)) is not None:
        if (row.kind, row.label) == (kind, label):
            ok, bounds = row.compute_bounds(window.sidebar.list)
            window.sidebar._right_click(Gtk.GestureClick(), 1, 40, bounds.get_y() + 5)
            pump(0.6)
            return window.sidebar._popover
        i += 1
    return None


def shot(window: NotesWindow, name: str) -> None:
    if SNAPSHOTS:
        pump(0.3)
        snapshot(window, str(SNAPSHOTS / f"{name}.png"))


def main() -> int:
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text(f'[notes]\nfolder = "{NOTES}"\n')
    app = Adw.Application(application_id="io.github.jinwei.LauncherNotesTest")
    app.register(None)

    window = NotesWindow(app)
    window.present()
    pump(1.0)
    check("notes folder is created", NOTES.is_dir())
    check("empty state without notes", window._stack.get_visible_child_name() == "empty")

    # Autosave after a pause, renamed after the title.
    window.new_note()
    check("new note file exists", (NOTES / "Untitled.md").exists())
    type_text(window, "# Lecture 3\n\nFourier series")
    pump(AUTOSAVE_DELAY_MS / 1000 - 0.5)
    check("nothing saved before the pause", (NOTES / "Untitled.md").read_text() == "")
    ok = wait_for(lambda: (NOTES / "Lecture 3.md").exists(), 2.0)
    check("autosaves 1 s after typing, named after the title", ok, str(list(NOTES.iterdir())))
    check("old untitled file is gone", not (NOTES / "Untitled.md").exists())
    check("window title follows", window._title.get_title() == "Lecture 3")

    # Focus moves to another window: saved right away.
    type_text(window, "\n- converges")
    other = Gtk.Window(title="Slides", application=app)
    other.present()
    ok = wait_for(lambda: "converges" in (NOTES / "Lecture 3.md").read_text(), 0.6)
    check("saves as soon as another window is focused", ok)
    window.present()
    pump(0.5)

    # Nothing written when nothing changed.
    before = (NOTES / "Lecture 3.md").stat().st_mtime_ns
    other.present()
    pump(0.5)
    window.present()
    pump(0.5)
    check(
        "no write when the text is unchanged", (NOTES / "Lecture 3.md").stat().st_mtime_ns == before
    )

    # Sidebar: Recent, pins, folders.
    rows = sidebar_rows(window)
    check("sidebar lists the note", ("note", "Lecture 3") in rows, repr(rows))
    window.activate_action("win.pin-note", GLib.Variant("s", "Lecture 3.md"))
    pump(0.2)
    rows = sidebar_rows(window)
    check(
        "pinned section appears",
        rows[:2] == [("header", "Pinned"), ("note", "Lecture 3")],
        repr(rows),
    )
    window.store.create_folder("", "Signals")
    window.store.create("Signals", "# Week 1\n")
    wait_for(lambda: ("folder", "Signals") in sidebar_rows(window), 1.0)
    check("new folder shows up", ("folder", "Signals") in sidebar_rows(window))
    window._toggle_folder("Signals")
    rows = sidebar_rows(window)
    i = rows.index(("folder", "Signals"))
    check("expanding a folder shows its notes", rows[i + 1] == ("note", "Week 1"), repr(rows))
    shot(window, "notes-sidebar")

    # Right-click menus: fully shown (no scrolling), and safe while the sidebar rebuilds.
    for kind, label in (("note", "Lecture 3"), ("folder", "Signals")):
        menu = open_menu(window, kind, label)
        check(f"{kind} menu opens", menu is not None and menu.get_visible())
        if menu is None:
            continue
        viewport = menu.get_child()
        content = viewport.get_first_child().get_first_child()
        check(
            f"{kind} menu shows every item without scrolling",
            content.get_height() <= viewport.get_height(),
            f"content {content.get_height()} > viewport {viewport.get_height()}",
        )
        window.refresh()  # e.g. an autosave or another app's change while it is open
        pump(0.3)
        check(f"{kind} menu stays open through a refresh", menu.get_visible())
        menu.popdown()
        pump(0.3)
        check(f"{kind} menu is detached once closed", menu.get_parent() is None)
    menu = open_menu(window, "note", "Lecture 3")
    window.activate_action("win.unpin-note", GLib.Variant("s", "Lecture 3.md"))
    menu.popdown()  # what choosing an item does
    ok = wait_for(lambda: ("header", "Pinned") not in sidebar_rows(window), 1.0)
    check("choosing a menu item updates the sidebar", ok, repr(sidebar_rows(window)))
    window.activate_action("win.pin-note", GLib.Variant("s", "Lecture 3.md"))
    pump(0.2)

    # Another app changes the open note while it has no unsaved edits: reloaded.
    (NOTES / "Lecture 3.md").write_text("# Lecture 3\n\nedited elsewhere\n")
    ok = wait_for(lambda: "edited elsewhere" in window._text(), 2.0)
    check("reloads a note changed by another app", ok, repr(window._text()))

    # ...and while it has unsaved edits: a banner, mine kept.
    type_text(window, "mine")
    (NOTES / "Lecture 3.md").write_text("# Lecture 3\n\ntheirs\n")
    ok = wait_for(lambda: window._banner.get_revealed(), 2.0)
    check("conflict shows a banner and keeps my text", ok and "mine" in window._text())
    window.save_now()
    check("saving mine clears the banner", not window._banner.get_revealed())

    # An empty new note is dropped when leaving it.
    window.new_note()
    empty = window.session.rel
    window.open_note("Lecture 3.md")
    check("empty new note is discarded", not (NOTES / empty).exists(), empty)

    # Deleting a note from the sidebar menu. GIO can't trash files under /tmp (where
    # this session keeps its notes): the note must then stay, not vanish.
    week = NOTES / "Signals/Week 1.md"
    window.activate_action("win.delete-note", GLib.Variant("s", "Signals/Week 1.md"))
    pump(0.3)
    check("a note that can't be trashed is kept", week.exists())
    trash = NOTES.parent / "FakeTrash"
    trash.mkdir()
    window.store._trash = lambda path: path.rename(trash / path.name)
    window.activate_action("win.delete-note", GLib.Variant("s", "Signals/Week 1.md"))
    pump(0.3)
    check(
        "delete moves the note to the Trash", not week.exists() and (trash / "Week 1.md").exists()
    )

    # Closing saves and remembers; a new window reopens the last note.
    type_text(window, "\nlast words")
    window.close()
    pump(0.5)
    check("closing saves", "last words" in (NOTES / "Lecture 3.md").read_text())
    state = json.loads(state_file().read_text())
    check("window state remembered", state.get("last") == "Lecture 3.md", repr(state))
    window = NotesWindow(app)
    window.present()
    pump(0.8)
    check(
        "reopens the last note",
        window.session is not None and window.session.rel == "Lecture 3.md",
    )
    shot(window, "notes-editor")

    # Launcher Settings picks another notes folder: Notes (running all session) switches.
    type_text(window, "\nbefore the switch")
    other = NOTES.parent / "Other Notes"
    other.mkdir()
    (other / "Elsewhere.md").write_text("# Elsewhere\n")
    paths.config_file().write_text(f'[notes]\nfolder = "{other}"\n')
    check("a new notes folder is used at once", wait_for(lambda: window.store.root == other))
    check("…after saving the open note in the old one",
          "before the switch" in (NOTES / "Lecture 3.md").read_text())  # fmt: skip
    check("…and the sidebar lists its notes", ("note", "Elsewhere") in sidebar_rows(window),
          repr(sidebar_rows(window)))  # fmt: skip
    window.open_note(str(other / "Elsewhere.md"))  # what the launcher's search passes
    check("a note from the new folder opens",
          window.session is not None and window.session.rel == "Elsewhere.md")  # fmt: skip
    paths.config_file().write_text(f'[notes]\nfolder = "{NOTES}"\n')
    check("…and back", wait_for(lambda: window.store.root == NOTES))

    # Narrow window: the sidebar overlays instead of taking space.
    window.unmaximize()
    window.set_default_size(560, 700)
    ok = wait_for(lambda: window.split.get_collapsed(), 2.0)
    check("narrow window collapses the sidebar", ok)
    window.close()
    pump(0.3)

    # `launcher --notes` from the command line: one instance, second call forwarded.
    launcher = str(ROOT / ".venv/bin/launcher")
    first = subprocess.Popen([launcher, "--notes"], stderr=subprocess.DEVNULL)
    time.sleep(2.5)
    started = time.monotonic()
    second = subprocess.run([launcher, "--notes", "--open", "Lecture 3.md"], timeout=15)
    elapsed = time.monotonic() - started
    check("second `launcher --notes` hands over and exits", second.returncode == 0 and elapsed < 5)
    check("first instance keeps running", first.poll() is None)
    first.terminate()
    first.wait(5)

    passed = sum(ok for _, ok in results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
