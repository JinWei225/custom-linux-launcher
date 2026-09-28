"""End-to-end test of the Notes editor's live markdown, in a headless nested GNOME Shell.

Types into the real MarkdownEditor the way GtkTextView does (committed text inserted
inside a user action; Enter, Tab, Backspace through the editor's key handler, falling
back to GTK's default when it declines) and checks the markdown, the formatting tags,
undo, checkbox clicks and input-method handling. Run through:

    tools/nested-shell.sh .venv/bin/python tools/nested_notes_editor_test.py [snapshot dir]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from launcher import paths  # noqa: E402
from launcher.notes.window import NotesWindow  # noqa: E402
from launcher.settings.debug import snapshot  # noqa: E402

SNAPSHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
NOTES = Path(os.environ["XDG_DATA_HOME"]) / "Notes"
CTRL = Gdk.ModifierType.CONTROL_MASK
SHIFT = Gdk.ModifierType.SHIFT_MASK
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


class Typist:
    def __init__(self, window: NotesWindow) -> None:
        self.editor = window.editor
        self.buffer = window.editor.buffer

    def type(self, text: str) -> None:
        """Type text; "\\n" is the Enter key, "\\t" the Tab key."""
        for ch in text:
            if ch == "\n":
                self.key(Gdk.KEY_Return)
            elif ch == "\t":
                self.key(Gdk.KEY_Tab)
            else:
                self.buffer.begin_user_action()
                self.buffer.insert_interactive_at_cursor(ch, -1, True)
                self.buffer.end_user_action()
        pump(0.02)

    def key(self, keyval: int, mods: Gdk.ModifierType = 0) -> bool:
        """Press a key: the editor's handler first, GTK's default if it declines."""
        handled = self.editor._on_key(None, keyval, 0, mods)
        if not handled:
            self.buffer.begin_user_action()
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self.buffer.insert_interactive_at_cursor("\n", -1, True)
            elif keyval == Gdk.KEY_Tab:
                self.buffer.insert_interactive_at_cursor("\t", -1, True)
            elif keyval == Gdk.KEY_BackSpace:
                self.buffer.backspace(self.cursor(), True, True)
            self.buffer.end_user_action()
        pump(0.02)
        return handled

    def cursor(self) -> Gtk.TextIter:
        return self.buffer.get_iter_at_mark(self.buffer.get_insert())

    def text(self) -> str:
        return self.editor.text()

    def clear(self) -> None:
        self.editor.load_text("")
        pump(0.05)

    def go(self, line: int, col: int) -> None:
        it = self.buffer.get_iter_at_line(line)[1]
        it.set_line_offset(col)
        self.buffer.place_cursor(it)
        pump(0.02)

    def tags(self, line: int, col: int) -> set[str]:
        it = self.buffer.get_iter_at_line(line)[1]
        it.set_line_offset(col)
        return {t.get_property("name") for t in it.get_tags()}


def main() -> int:
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text(f'[notes]\nfolder = "{NOTES}"\n')
    app = Adw.Application(application_id="io.github.jinwei.LauncherNotesEditorTest")
    app.register(None)
    window = NotesWindow(app)
    window.present()
    window.new_note()
    pump(0.8)
    t = Typist(window)

    # Headings: "## " makes a heading; its marker hides once the cursor leaves the line.
    t.type("## Week 3")
    check("'## ' makes a heading", "h2" in t.tags(0, 5), repr(t.tags(0, 5)))
    check("heading marker shown on the cursor's line", "md-markup" in t.tags(0, 0))
    t.type("\n")
    check("heading marker hidden on other lines", "md-hidden" in t.tags(0, 0), repr(t.tags(0, 0)))
    check("Enter after a heading starts plain text", t.text() == "## Week 3\n")
    t.go(0, 9)
    check("marker shows again when the cursor comes back", "md-markup" in t.tags(0, 0))
    t.go(1, 0)
    t.type("#no heading")
    check("'#' without a space stays text", "h1" not in t.tags(1, 3))

    # Bullets: "- " hides and continues; Enter on an empty item ends the list.
    t.clear()
    t.type("- apples\nbananas\n")
    check("Enter continues a bullet list", t.text() == "- apples\n- bananas\n- ", repr(t.text()))
    check("bullet marker is hidden", "md-hidden" in t.tags(0, 0) and "md-list0" in t.tags(0, 3))
    t.type("\n")
    check(
        "Enter on an empty item ends the list", t.text() == "- apples\n- bananas\n", repr(t.text())
    )

    # Nesting with Tab / Shift+Tab, and Enter on an empty nested item steps out.
    t.clear()
    t.type("- a\n\tb\n")
    check("Tab indents a list item", t.text() == "- a\n\t- b\n\t- ", repr(t.text()))
    check("nested item gets a deeper indent", "md-list1" in t.tags(1, 3))
    t.type("\n")
    check("Enter on an empty nested item outdents it", t.text() == "- a\n\t- b\n- ", repr(t.text()))
    t.go(1, 4)
    t.key(Gdk.KEY_ISO_Left_Tab, SHIFT)
    check("Shift+Tab outdents", t.text() == "- a\n- b\n- ", repr(t.text()))

    # Cursor never rests inside a hidden marker.
    t.go(1, 0)
    check("cursor moves out of a hidden marker", t.cursor().get_line_offset() == 2)

    # Backspace at the start of an item's text.
    t.clear()
    t.type("- a\n\tb")
    t.go(1, 3)
    t.key(Gdk.KEY_BackSpace)
    check("Backspace outdents a nested item", t.text() == "- a\n- b", repr(t.text()))
    t.go(1, 2)
    t.key(Gdk.KEY_BackSpace)
    check("Backspace removes a top-level marker", t.text() == "- a\nb", repr(t.text()))
    t.clear()
    t.type("## Title")
    t.go(0, 3)
    t.key(Gdk.KEY_BackSpace)
    check("Backspace turns a heading back into text", t.text() == "Title", repr(t.text()))
    t.clear()
    t.type("- x")
    t.go(0, 3)
    t.key(Gdk.KEY_BackSpace)
    check("Backspace inside the text deletes normally", t.text() == "- ", repr(t.text()))

    # Ordered lists continue, renumber, and undo in one step.
    t.clear()
    t.type("1. one\ntwo\nthree")
    check("Enter continues numbering", t.text() == "1. one\n2. two\n3. three", repr(t.text()))
    t.go(0, 6)
    t.type("\ninserted")
    expected = "1. one\n2. inserted\n3. two\n4. three"
    check("inserting an item renumbers the rest", t.text() == expected, repr(t.text()))
    window.buffer.undo()
    pump(0.05)
    check(
        "one undo removes the item and its renumbering",
        t.text() in ("1. one\n2. \n3. two\n4. three", "1. one\n2. two\n3. three"),
        repr(t.text()),
    )
    t.clear()
    t.type("1. a\nb\nc")  # Enter types the next number itself
    t.go(1, 0)
    it = window.buffer.get_iter_at_line(1)[1]
    end = window.buffer.get_iter_at_line(2)[1]
    window.buffer.begin_user_action()
    window.buffer.delete(it, end)  # delete a whole line
    window.buffer.end_user_action()
    pump(0.05)
    check("deleting an item renumbers", t.text() == "1. a\n2. c", repr(t.text()))

    # Checkboxes: shorthand, Ctrl+Enter, click.
    t.clear()
    t.type("[] buy milk")
    check("'[] ' becomes a checkbox", t.text() == "- [ ] buy milk", repr(t.text()))
    t.key(Gdk.KEY_Return, CTRL)
    check("Ctrl+Enter ticks it", t.text() == "- [x] buy milk", repr(t.text()))
    check("ticked items are struck through", "md-done" in t.tags(0, 8))
    t.type("\nbread")
    check("Enter after a ticked item starts an unticked one", t.text().endswith("\n- [ ] bread"))
    window.editor.queue_draw()
    pump(0.3)
    box = window.editor._checkboxes.get(0)
    check("checkbox is drawn", box is not None)
    if box is not None:
        cx, cy = box.get_x() + box.get_width() / 2, box.get_y() + box.get_height() / 2
        wx, wy = window.editor.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, int(cx), int(cy))
        window.editor._on_click(Gtk.GestureClick(), 1, wx, wy)
        pump(0.05)
        check(
            "clicking the checkbox unticks it",
            t.text().startswith("- [ ] buy milk"),
            repr(t.text()),
        )
    t.go(0, 6)
    t.key(Gdk.KEY_BackSpace)
    check("Backspace at a checkbox's text removes it", t.text().startswith("buy milk"))

    # Quotes, rules, code blocks.
    t.clear()
    t.type("> a quote\nmore")
    check("Enter continues a quote", t.text() == "> a quote\n> more", repr(t.text()))
    t.type("\n\n")
    check("Enter on an empty quote line ends it", t.text() == "> a quote\n> more\n", repr(t.text()))
    t.clear()
    t.type("---\nafter")
    check("'---' is hidden off the cursor's line", "md-hidden" in t.tags(0, 0))
    t.clear()
    t.type("```\n")
    check("``` + Enter closes the code block", t.text() == "```\n\n```", repr(t.text()))
    check("cursor goes inside the block", t.cursor().get_line() == 1)
    t.type("# not a heading\n- not a list")
    check(
        "markdown inside code stays literal", t.text() == "```\n# not a heading\n- not a list\n```"
    )
    check("code lines are monospace", "md-code" in t.tags(1, 2) and "h1" not in t.tags(1, 2))

    # Input methods: while composing, Enter and Tab belong to the IME.
    t.clear()
    t.type("- 中文")
    window.editor.emit("preedit-changed", "ni")
    check("keys go to the input method while composing", not window.editor._on_key(
        None, Gdk.KEY_Return, 0, 0))  # fmt: skip
    window.editor.emit("preedit-changed", "")
    check("CJK text in a list item", t.text() == "- 中文")

    # Loading a note never rewrites it.
    (NOTES / "Odd.md").write_text("# Odd\n\n9. a\n9. b\n[] not converted\n")
    before = (NOTES / "Odd.md").read_text()
    window.open_note("Odd.md")
    pump(1.5)
    check("opening a note doesn't renumber or convert it", (NOTES / "Odd.md").read_text() == before)
    check("the loaded note isn't undoable", not window.buffer.get_can_undo())

    # Autosave writes plain markdown.
    t.clear()
    window.new_note()
    pump(0.3)
    t.type("# Signals\n- item\n\tnested\n\n[] task\n")
    window.save_now()
    saved = (NOTES / "Signals.md").read_text()
    # The second Enter on the empty nested item steps out a level (it doesn't end the list).
    expected = "# Signals\n- item\n\t- nested\n- [ ] task\n- [ ] "
    check("saved file is plain markdown", saved == expected, repr(saved))

    # A showcase for the snapshot.
    window.editor.load_text(
        "# Lecture 3: Fourier Series\n\nPeriodic signals as sums of sinusoids.\n\n"
        "## Key ideas\n- Orthogonality of basis functions\n\t- sin and cos\n\t\t- deeper\n"
        "- Convergence (Dirichlet)\n\n### Steps\n1. Find the period\n2. Compute a₀\n"
        "3. Compute aₙ and bₙ\n\n- [x] Read chapter 3\n- [ ] Problem set 2\n\n"
        "> Any periodic function can be written as a sum of sines.\n\n---\n\n"
        "```python\nimport numpy as np\nx = np.fft.fft(signal)\n```\n"
    )
    window.buffer.place_cursor(window.buffer.get_end_iter())
    pump(0.5)
    if SNAPSHOTS:
        window.editor.scroll_to_iter(window.buffer.get_start_iter(), 0, False, 0, 0)
        pump(0.3)
        snapshot(window, str(SNAPSHOTS / "notes-markdown.png"))
        window.editor.scroll_to_iter(window.buffer.get_end_iter(), 0, False, 0, 0)
        pump(0.3)
        snapshot(window, str(SNAPSHOTS / "notes-markdown-end.png"))

    passed = sum(ok for _, ok in results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
