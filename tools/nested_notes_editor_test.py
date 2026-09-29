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
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402

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


def wait(predicate, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(0.05)
        if predicate():
            return True
    return False


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

    # Numbered sub-lists, like Notion: 1. / a) / i. by level; the file keeps "1.".
    def labels() -> dict[int, str]:
        window.editor.queue_draw()
        pump(0.15)
        return {n: label for n, (label, _x, _y) in window.editor._numbers.items()}

    t.clear()
    t.type("1. first\n\tsub one\nsub two\n\tdeeper\n\n\nsecond")
    expected = "1. first\n\t1. sub one\n\t2. sub two\n\t\t1. deeper\n2. second"
    check("Tab starts a sub-list; Enter twice steps back out", t.text() == expected,
          repr(t.text()))  # fmt: skip
    check("levels show as 1. / a) / i.",
          labels() == {0: "1.", 1: "a)", 2: "b)", 3: "i.", 4: "2."}, repr(labels()))  # fmt: skip
    check("the number is hidden text, drawn in the margin",
          "md-hidden" in t.tags(1, 2) and "md-hidden" not in t.tags(1, 4))  # fmt: skip
    t.go(1, 4)
    t.key(Gdk.KEY_Home)
    check("Home goes to the text, not into the number", t.cursor().get_line_offset() == 4)
    t.clear()
    t.type("1. a\nb\nc")
    t.go(1, 4)
    t.key(Gdk.KEY_Tab)
    check("Tab on an item renumbers the rest", t.text() == "1. a\n\t1. b\n2. c", repr(t.text()))
    t.clear()
    t.type("1. top\n\ta) typed")
    check("typing a) on the new sub-item keeps it numbered",
          t.text() == "1. top\n\t1. typed" and labels().get(1) == "a)", repr(t.text()))  # fmt: skip
    t.clear()
    t.type("1. top\n")
    t.key(Gdk.KEY_BackSpace)  # a plain line under the list
    t.type("\ta) typed")
    check("typing a) on an indented line starts a sub-list",
          t.text() == "1. top\n\t1. typed" and labels().get(1) == "a)", repr(t.text()))  # fmt: skip

    # Mixed: a bullet under a numbered item, then back to the next number.
    t.clear()
    t.type("1. first\n\t- bullet\n\nsecond")
    check("- on an empty sub-item makes it a bullet, Enter twice goes back to 2.",
          t.text() == "1. first\n\t- bullet\n2. second", repr(t.text()))  # fmt: skip
    check("…numbered 1., 2. around the bullet", labels() == {0: "1.", 2: "2."}, repr(labels()))
    t.clear()
    t.type("- bullet\n\t1. numbered")
    check("a numbered list inside a bullet starts at 1.", labels() == {1: "1."}, repr(labels()))

    # Shorthands leave rules, prose after a bullet, and code alone.
    t.clear()
    t.type("- - -")
    check("'- - -' can be typed (a rule)", t.text() == "- - -", repr(t.text()))
    t.clear()
    t.type("- a) first option")
    check("'- a) first option' stays a bullet with its text",
          t.text() == "- a) first option", repr(t.text()))  # fmt: skip
    t.clear()
    t.type("```yaml\n  - * x\n\ta) y")
    check("shorthands don't rewrite code", t.text() == "```yaml\n  - * x\n\ta) y\n```",
          repr(t.text()))  # fmt: skip

    # A long numbered item wraps under its text, not under the number.
    t.clear()
    t.type("1. " + "wrapping words " * 30)
    pump(0.2)
    first = window.editor.get_iter_location(window.buffer.get_iter_at_line_offset(0, 3)[1])
    wrapped = None
    it = window.buffer.get_iter_at_line_offset(0, 3)[1]
    while not it.ends_line():
        rect = window.editor.get_iter_location(it)
        if rect.y > first.y + 5:
            wrapped = rect
            break
        it.forward_char()
    label, right, _baseline = window.editor._numbers[0]
    check("a long item wraps", wrapped is not None)
    check("…and its next row starts where the text does, right of the number",
          wrapped is not None and abs(wrapped.x - first.x) <= 2 and right < first.x,
          f"text {first.x}, wrapped row {wrapped and wrapped.x}, number ends {right}")  # fmt: skip

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

    # Bullets and checkboxes stay put when you start typing after them, and sit on the
    # same line as their text.
    def drawn(text: str) -> tuple[float | None, Graphene.Rect | None]:
        window.editor.load_text(text)
        window.editor.queue_draw()
        pump(0.3)
        return window.editor._bullets.get(0), window.editor._checkboxes.get(0)

    empty_bullet, _ = drawn("- ")
    full_bullet, _ = drawn("- word")
    check("bullet doesn't move when typing after it", empty_bullet == full_bullet,
          f"{empty_bullet} vs {full_bullet}")  # fmt: skip
    layout = window.editor.create_pango_layout("Ag")
    text_baseline = layout.get_baseline() / Pango.SCALE  # first row starts at y = 0
    check("bullet sits on the text's baseline", abs(full_bullet - text_baseline) < 0.5,
          f"{full_bullet} vs {text_baseline}")  # fmt: skip
    _, empty_box = drawn("- [ ] ")
    _, full_box = drawn("- [ ] Word")
    check(
        "checkbox doesn't move when typing after it",
        empty_box.get_y() == full_box.get_y(),
        f"{empty_box.get_y()} vs {full_box.get_y()}",
    )
    cap = window.editor._cap_height()
    middle = full_box.get_y() + full_box.get_height() / 2
    check("checkbox is centred on its text", abs(middle - (text_baseline - cap / 2)) <= 1,
          f"box middle {middle}, text middle {text_baseline - cap / 2}")  # fmt: skip
    cjk_bullet, _ = drawn("- 中文笔记")
    cjk_layout = window.editor.create_pango_layout("Ag中文笔记")
    check("bullet follows a taller CJK row's baseline",
          abs(cjk_bullet - cjk_layout.get_baseline() / Pango.SCALE) < 0.5)  # fmt: skip
    if SNAPSHOTS:
        window.editor.load_text(
            "- \n- word\n- [ ] \n- [ ] Word here\n- [x] Done item\n- 中文笔记\n"
        )
        pump(0.4)
        snapshot(window, str(SNAPSHOTS / "notes-alignment.png"))

    # Quotes need the space after ">".
    t.clear()
    t.type(">")
    check("'>' alone is not a quote yet", ">" in t.text() and "md-hidden" not in t.tags(0, 0))
    t.type(" ")
    check("'> ' makes a quote", "md-hidden" in t.tags(0, 0) and "md-quote1" in t.tags(0, 0))

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

    # Inline styles: applied once the closing marker is typed; markers hidden elsewhere.
    t.clear()
    t.type("some **bold")
    check("unclosed ** stays plain", "md-bold" not in t.tags(0, 8))
    t.type("** text")
    check("closing ** makes it bold", "md-bold" in t.tags(0, 8), repr(t.tags(0, 8)))
    check("inline markers shown on the cursor's line", "md-markup" in t.tags(0, 5))
    t.type("\n")
    check("inline markers hidden on other lines", "md-hidden" in t.tags(0, 5))
    for typed, col, tag in (
        ("*it*", 1, "md-italic"),
        ("_it_", 1, "md-italic"),
        ("~~gone~~", 2, "md-strike"),
        ("`x = 1`", 1, "md-inline-code"),
        ("==key==", 2, "md-highlight"),
        ("<u>under</u>", 3, "md-underline"),
        ("- **in a list**", 4, "md-bold"),
        ("## **heading**", 5, "md-bold"),
    ):
        t.clear()
        t.type(typed)
        check(f"{typed} is styled", tag in t.tags(0, col), repr(t.tags(0, col)))
    t.clear()
    t.type("```\n**not bold**")
    check("no inline styles in code blocks", "md-bold" not in t.tags(1, 3))

    # Shortcuts wrap the selection, and unwrap it again.
    t.clear()
    t.type("make this bold")
    t.go(0, 5)
    it = window.buffer.get_iter_at_line(0)[1]
    it.set_line_offset(9)
    window.buffer.select_range(t.cursor(), it)
    t.key(Gdk.KEY_b, CTRL)
    check("Ctrl+B wraps the selection", t.text() == "make **this** bold", repr(t.text()))
    bounds = window.buffer.get_selection_bounds()
    check("the words stay selected", bounds and window.buffer.get_text(*bounds, True) == "this")
    t.key(Gdk.KEY_b, CTRL)
    check("Ctrl+B again unwraps it", t.text() == "make this bold", repr(t.text()))
    t.key(Gdk.KEY_i, CTRL)
    check("Ctrl+I italic", t.text() == "make *this* bold", repr(t.text()))
    window.buffer.undo()
    pump(0.05)
    check("one undo removes a style", t.text() == "make this bold", repr(t.text()))
    for key, shift, expected in (
        (Gdk.KEY_u, False, "make <u>this</u> bold"),
        (Gdk.KEY_e, False, "make `this` bold"),
        (Gdk.KEY_X, True, "make ~~this~~ bold"),
        (Gdk.KEY_H, True, "make ==this== bold"),
    ):
        t.clear()
        t.type("make this bold")
        t.go(0, 5)
        end = window.buffer.get_iter_at_line(0)[1]
        end.set_line_offset(9)
        window.buffer.select_range(t.cursor(), end)
        t.key(key, CTRL | SHIFT if shift else CTRL)
        check(f"shortcut makes {expected!r}", t.text() == expected, repr(t.text()))
    t.clear()
    t.type("x")
    t.key(Gdk.KEY_b, CTRL)
    t.type("new")
    check("Ctrl+B with nothing selected: type inside", t.text() == "x**new**", repr(t.text()))

    # Links: Ctrl+K, styling, what is under the pointer, opening.
    t.clear()
    t.type("read the docs")
    t.go(0, 9)
    end = window.buffer.get_iter_at_line(0)[1]
    end.set_line_offset(13)
    window.buffer.select_range(t.cursor(), end)
    t.key(Gdk.KEY_k, CTRL)
    t.type("https://gnome.org")
    check("Ctrl+K makes a link, cursor in the address",
          t.text() == "read the [docs](https://gnome.org)", repr(t.text()))  # fmt: skip
    window.buffer.place_cursor(window.buffer.get_end_iter())  # out of the link, then Enter
    t.type("\n")
    check("link text styled", "md-link" in t.tags(0, 10))
    check("link address hidden off the cursor's line", "md-hidden" in t.tags(0, 16))
    pump(0.2)
    location = window.editor.get_iter_location(window.buffer.get_iter_at_line_offset(0, 11)[1])
    url = window.editor.link_at(location.x + 1, location.y + location.height // 2)
    check("link found under the pointer", url == "https://gnome.org", repr(url))
    opened = []
    window.editor.link_handler = opened.append
    t.clear()
    t.type("see https://example.com/page and more\n")
    pump(0.2)
    location = window.editor.get_iter_location(window.buffer.get_iter_at_line_offset(0, 8)[1])
    url = window.editor.link_at(location.x + 1, location.y + location.height // 2)
    check("bare URLs are links", url == "https://example.com/page", repr(url))
    window.editor.link_handler = window._open_link
    (NOTES / "Other.md").write_text("# Other\n")
    window._open_link("Other.md")
    pump(0.3)
    check("a link to a note opens it here", window.session.rel == "Other.md")
    window.new_note()
    pump(0.3)

    # Pictures: a pasted screenshot is saved next to the note and shown.
    t.clear()
    window.editor.note_stem = "Lecture 3"
    t.type("before")
    pixels = GLib.Bytes.new(bytes([200, 60, 40, 255]) * (400 * 200))
    texture = Gdk.MemoryTexture.new(400, 200, Gdk.MemoryFormat.R8G8B8A8, pixels, 400 * 4)
    window.editor.get_clipboard().set(texture)
    pump(0.2)
    window.editor.emit("paste-clipboard")
    ok = wait(lambda: "![](attachments/" in t.text(), 3.0)
    check("pasting a picture inserts a link", ok, repr(t.text()))
    attachments = sorted((window.editor.base_dir / "attachments").glob("*.png"))
    check("the picture is saved in attachments/", len(attachments) == 1, repr(attachments))
    if attachments:
        name = attachments[0].name
        check("named after the note", name.startswith("Lecture-3-"), name)
        check("link on its own line, cursor below",
              t.text() == f"before\n![](attachments/{name})\n" and t.cursor().get_line() == 2,
              repr(t.text()))  # fmt: skip
        pump(0.3)
        tags = t.tags(1, 0)
        check("room is made below the line", any(n is None for n in tags) or len(tags) > 1)
        y, height = window.editor.get_line_yrange(window.buffer.get_iter_at_line(1)[1])
        check("the picture's height is reserved", height >= 200, f"line height {height}")
        check("the link text is hidden off the cursor's line", "md-hidden" in t.tags(1, 2))
        t.go(1, 3)
        check("the link shows on the cursor's line", "md-markup" in t.tags(1, 2))
    # The way a screenshot tool offers it: PNG bytes, no text.
    png = texture.save_to_png_bytes()
    window.editor.get_clipboard().set_content(Gdk.ContentProvider.new_for_bytes("image/png", png))
    pump(0.2)
    window.buffer.place_cursor(window.buffer.get_end_iter())
    window.editor.emit("paste-clipboard")
    ok = wait(lambda: t.text().count("![](attachments/") == 2, 3.0)
    check("a screenshot (image/png) pastes as a picture", ok, repr(t.text()))
    window.editor.get_clipboard().set("plain text")
    pump(0.2)
    t.go(0, 6)
    window.editor.emit("paste-clipboard")
    ok = wait(lambda: "beforeplain text" in t.text(), 2.0)
    check("pasting text still pastes text", ok, repr(t.text()))

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
        "> Any periodic function can be written as a sum of sines.\n\n"
        "**Bold**, *italic*, ~~struck~~, `inline code`, ==highlighted== and <u>underlined</u>; "
        "see [the lecture page](https://example.edu/signals) or https://gnome.org.\n\n"
        + (f"![](attachments/{attachments[0].name})\n" if attachments else "")
        + "Text after the picture.\n\n---\n\n"
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
