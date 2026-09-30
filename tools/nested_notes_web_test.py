"""The CodeMirror Notes editor (web_editor.py, a prototype) under real keyboard and
pointer input, in a headless nested GNOME Shell. Types lists, numbers, checkboxes and
styles like a person and checks the markdown that results, the autosaved file, and
screenshots. Run through:

    tools/nested-shell.sh .venv/bin/python tools/nested_notes_web_test.py [out-dir]

Screenshots go to out-dir (default: the nested shell's temporary folder).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

os.environ["LAUNCHER_NOTES_EDITOR"] = "web"

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from launcher import paths  # noqa: E402
from launcher.notes.window import NotesWindow  # noqa: E402
from launcher.settings.debug import snapshot  # noqa: E402

NOTES = Path(os.environ["XDG_DATA_HOME"]) / "Notes"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ["NESTED_TMP"])
NOTE = "# Lecture 3\n\nSee [docs](https://example.com/docs) first.\n"
SHIFTED = set('~!@#$%^&*()_+{}|:"<>?') | set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    sys.stdout.flush()


def pump(seconds: float) -> None:
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def wait_for(predicate, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        pump(0.05)
    return predicate()


class Input:
    def __init__(self, window: NotesWindow) -> None:
        self.window = window
        self.view = window.editor.view
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    def _call(self, method: str, args: GLib.Variant):
        return self.bus.call_sync(
            "org.gnome.Shell", "/io/github/jinwei/NestedInput", "io.github.jinwei.NestedInput",
            method, args, None, Gio.DBusCallFlags.NONE, 3000, None,
        ).unpack()  # fmt: skip

    def js(self, script: str):
        box = []
        self.view.evaluate_javascript(
            script, -1, None, None, None,
            lambda view, result: box.append(view.evaluate_javascript_finish(result)),
        )  # fmt: skip
        wait_for(lambda: box, 3.0)
        return json.loads(box[0].to_json(0)) if box else None

    def screen(self, n: int, col: int) -> tuple[float, float]:
        page = self.js(f"notes.coords({n}, {col})")
        where = Graphene.Point().init(page["x"], page["y"])
        ok, point = self.view.compute_point(self.window, where)
        frame = self._call("WindowFrame", GLib.Variant("(s)", (self.window.get_title(),)))[0]
        return frame[0] + point.x, frame[1] + point.y

    def click(self, x: float, y: float, ctrl: bool = False) -> None:
        self._call("Move", GLib.Variant("(dd)", (x, y)))
        pump(0.05)
        if ctrl:
            self.key(Gdk.KEY_Control_L, True)
        for state in (True, False):
            self._call("Button", GLib.Variant("(ub)", (1, state)))
            pump(0.03)
        if ctrl:
            self.key(Gdk.KEY_Control_L, False)
        pump(0.3)

    def key(self, keyval: int, pressed: bool | None = None) -> None:
        for state in (True, False) if pressed is None else (pressed,):
            self._call("Key", GLib.Variant("(ub)", (keyval, state)))
        pump(0.02)

    def combo(self, *keyvals: int) -> None:
        for k in keyvals:
            self.key(k, True)
        for k in reversed(keyvals):
            self.key(k, False)
        pump(0.1)

    def type(self, text: str) -> None:
        for ch in text:
            if ch == "\n":
                self.key(Gdk.KEY_Return)
            elif ch == "\t":
                self.key(Gdk.KEY_Tab)
            elif ch in SHIFTED:
                self.combo(Gdk.KEY_Shift_L, Gdk.unicode_to_keyval(ord(ch)))
            else:
                self.key(Gdk.unicode_to_keyval(ord(ch)))
        pump(0.3)


def input_methods(io: Input, editor) -> None:
    """Pinyin and Hangul through IBus, as typed on this machine: the composed text lands
    once, where the cursor is, and Enter while composing belongs to the input method."""
    if shutil.which("ibus-daemon") is None:
        print("SKIP input methods: no ibus-daemon")
        return
    subprocess.run(["ibus-daemon", "--daemonize", "--replace", "--xim"], check=False)
    subprocess.run(
        [
            "gsettings",
            "set",
            "org.gnome.desktop.input-sources",
            "sources",
            "[('xkb', 'us'), ('ibus', 'libpinyin'), ('ibus', 'hangul')]",
        ],
        check=False,
    )
    subprocess.run(["gsettings", "set", "org.freedesktop.ibus.engine.hangul",
                    "initial-input-mode", "hangul"], check=False)  # fmt: skip
    pump(3.0)

    def lines() -> list[str]:
        return editor.text().split("\n")

    io.key(Gdk.KEY_End)
    io.type("\n- ")
    row = editor.cursor_line()
    for index, keys, want in ((1, "nihao ", "你好"), (2, "gksrmf ", "한글")):
        source = io._call("SetInputSource", GLib.Variant("(u)", (index,)))[0]
        pump(1.5)
        if not source:
            print(f"SKIP input source {index}: not available")
            continue
        io.type(keys)
        pump(0.5)
        check(f"{source}: {keys.strip()} gives {want} in a list item", want in lines()[row],
              repr(lines()[row]))  # fmt: skip
    io._call("SetInputSource", GLib.Variant("(u)", (0,)))
    pump(1.0)
    io.type("ok")
    check("typing goes on after the input methods", lines()[row].endswith("ok"),
          repr(lines()[row]))  # fmt: skip


def long_note(io: Input, window: NotesWindow) -> None:
    """A 10,000-line note with a 500-item list: Enter in the list renumbers all of it,
    and a keystroke reaches Python in about the time it takes the GTK editor (~40 ms
    here, against ~60 ms for the GTK one; the nested shell adds to both)."""
    text = ["# Long note", ""]
    text += [f"{i}. item {i} with **bold** and a [link](https://x.org/{i})" for i in range(1, 501)]
    n = 0
    while len(text) < 10000:
        n += 1
        text += [f"## Section {n}", "", f"Paragraph {n} with *italic*, `code` and ==marks==."]
        text += ["- bullet", "\t- nested", "- [ ] task", "> quote", "```", "code", "```", ""]
        text += [f"Plain line {n}.{k} of prose that goes on for a while." for k in range(30)]
    (NOTES / "Long.md").write_text("\n".join(text[:10000]) + "\n")
    editor = window.editor
    window.open_note("Long.md")
    check("a 10,000-line note opens", wait_for(lambda: editor.text().count("\n") == 10000))

    def lines() -> list[str]:
        return editor.text().split("\n")

    editor.go_to_line(12)  # "11. item 11 ..."
    pump(0.5)
    io.key(Gdk.KEY_End)
    io.type("\nnew")
    wait_for(lambda: lines()[13] == "12. new", 2.0)
    check("Enter in a long list inserts its next number", lines()[13] == "12. new",
          repr(lines()[13]))  # fmt: skip
    check("and renumbers the 490 items after it", lines()[502].startswith("501. item 500"),
          repr(lines()[502][:20]))  # fmt: skip

    editor.go_to_line(5000)
    pump(0.5)
    io.key(Gdk.KEY_End)
    context = GLib.MainContext.default()
    times = []
    for _ in range(20):
        size = len(editor.text())
        start = time.monotonic()
        io._call("Key", GLib.Variant("(ub)", (Gdk.KEY_z, True)))
        io._call("Key", GLib.Variant("(ub)", (Gdk.KEY_z, False)))
        while len(editor.text()) == size and time.monotonic() < start + 2:
            context.iteration(False) or time.sleep(0.001)
        times.append((time.monotonic() - start) * 1000)
        pump(0.05)
    median = sorted(times)[len(times) // 2]
    print(f"keystroke in a 10,000-line note: median {median:.0f} ms, max {max(times):.0f} ms")
    check("typing in a long note stays quick", median < 100, f"median {median:.0f} ms")
    window.save_now()
    check("the long note saves as typed", (NOTES / "Long.md").read_text() == editor.text())


def main() -> int:
    NOTES.mkdir(parents=True, exist_ok=True)
    (NOTES / "Test.md").write_text(NOTE)
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text(f'[notes]\nfolder = "{NOTES}"\n')
    app = Adw.Application(application_id="io.github.jinwei.LauncherNotesWebTest")
    app.register(None)
    window = NotesWindow(app)
    window.set_default_size(1000, 720)
    window.present()
    pump(1.0)
    window.open_note("Test.md")
    editor = window.editor
    check("the web editor is in use", type(editor).__name__ == "WebMarkdownEditor")
    check("the page starts", wait_for(lambda: editor._ready, 10.0))
    pump(0.5)
    io = Input(window)

    def lines() -> list[str]:
        return editor.text().split("\n")

    def expect(name: str, want: list[str], start: int) -> None:
        # Keys reach the page through the compositor: give the last one time to land.
        wait_for(lambda: lines()[start : start + len(want)] == want, 2.0)
        got = lines()[start : start + len(want)]
        check(name, got == want, f"{got!r}")

    # Click at the end of the last line and type below it.
    io.click(*io.screen(2, 200))
    io.type("\n")
    start = editor.cursor_line()

    io.type("- one\ntwo\n\tthree\n\n\nafter\n")
    expect("bullets continue, Tab nests, Enter on empty items steps out",
           ["- one", "- two", "\t- three", "after", ""], start)  # fmt: skip

    start = editor.cursor_line()
    io.type("1. a\nb\nc\n\n")
    expect("numbered items continue and an empty one ends the list",
           ["1. a", "2. b", "3. c", ""], start)  # fmt: skip

    # Tab on "2. b": it becomes "a)" one level in, and "3. c" becomes "2. c".
    io.click(*io.screen(start + 1, 3))
    io.key(Gdk.KEY_End)
    io.key(Gdk.KEY_Tab)
    expect("Tab makes a sub-item and renumbers", ["1. a", "\ta) b", "2. c"], start)
    io.combo(Gdk.KEY_Control_L, Gdk.KEY_z)
    expect("one Ctrl+Z undoes the indent and the renumbering", ["1. a", "2. b", "3. c"], start)

    # Backspace at the start of an item's text removes its marker.
    io.key(Gdk.KEY_Home)
    io.key(Gdk.KEY_BackSpace)
    expect("Backspace at an item's start removes the marker", ["1. a", "b", "3. c"], start)
    io.combo(Gdk.KEY_Control_L, Gdk.KEY_z)
    expect("Ctrl+Z brings the marker back", ["1. a", "2. b", "3. c"], start)

    # Selecting from one item's text into the next and deleting joins them: the list
    # renumbers.
    io.click(*io.screen(start, 3))
    io.combo(Gdk.KEY_Shift_L, Gdk.KEY_Down)
    io.key(Gdk.KEY_BackSpace)
    expect("the list renumbers when an item goes", ["1. b", "2. c"], start)
    io.combo(Gdk.KEY_Control_L, Gdk.KEY_z)

    # Checkboxes: the shorthand, Ctrl+Enter and a click on the box.
    io.click(*io.screen(len(lines()) - 1, 0))
    start = editor.cursor_line()
    io.type("[] buy milk")
    expect("[] becomes a checkbox", ["- [ ] buy milk"], start)
    io.combo(Gdk.KEY_Control_L, Gdk.KEY_Return)
    expect("Ctrl+Enter ticks it", ["- [x] buy milk"], start)
    x, y = io.screen(start, 6)  # the middle of the "b"; the box is centred 14px before it
    io.click(x - 5 - 14, y)
    expect("a click on the box unticks it", ["- [ ] buy milk"], start)
    io.key(Gdk.KEY_End)
    io.type("\n\n")

    # Styles typed and by shortcut.
    start = editor.cursor_line()
    io.type("some **bold** and ")
    io.combo(Gdk.KEY_Control_L, Gdk.KEY_b)
    io.type("more")
    expect("bold typed and with Ctrl+B", ["some **bold** and **more**"], start)
    io.key(Gdk.KEY_End)
    io.type("\n## A heading\nbody")
    expect("headings", ["## A heading", "body"], start + 1)
    outline = [h.text for h in editor.headings()]
    check("the outline sees new headings", outline == ["Lecture 3", "A heading"], repr(outline))

    # Ctrl+click opens a link.
    opened: list[str] = []
    editor.link_handler = opened.append
    io.click(*io.screen(2, 6), ctrl=True)
    check("Ctrl+click opens the link", opened == ["https://example.com/docs"], repr(opened))

    # Autosave writes what the page holds.
    pump(2.5)
    window.save_now()
    saved = (NOTES / "Test.md").read_text()
    check("the note is saved as typed", saved == editor.text(), repr(saved[-80:]))

    input_methods(io, editor)

    io.click(*io.screen(0, 3))
    pump(0.5)
    snapshot(window, str(OUT / "web-notes.png"))
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    pump(1.0)
    snapshot(window, str(OUT / "web-notes-dark.png"))
    print("screenshots in", OUT)
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)

    long_note(io, window)

    failed = [name for name, ok in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    window.shut_down()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
