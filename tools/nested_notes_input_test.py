"""The Notes editor under real pointer and keyboard input, in a headless nested GNOME Shell.

Uses the test-only nested-input extension (tools/nested-input@jinwei.github.io, which
tools/nested-shell.sh installs) to move the pointer, click and type like a person:
clicking into hidden markup on other lines, hovering links, Ctrl+click, checkbox clicks,
arrow keys around hidden list markers. A GTK crash ends this process, which the nested
shell reports as a failure. Run through:

    tools/nested-shell.sh .venv/bin/python tools/nested_notes_input_test.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Graphene, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from launcher import paths  # noqa: E402
from launcher.notes.window import NotesWindow  # noqa: E402

NOTES = Path(os.environ["XDG_DATA_HOME"]) / "Notes"
NOTE = """\
# Heading here

see [docs](https://example.com/very/long/path/here)
Some **bold** words and ~~struck~~ ones
- item **one**
- [ ] a task
plain line with https://gnome.org inside
"""
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


class Input:
    """Real input through the nested-input extension, aimed at editor positions."""

    def __init__(self, window: NotesWindow) -> None:
        self.window = window
        self.editor = window.editor
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        frame = self._call("WindowFrame", GLib.Variant("(s)", (window.get_title(),)))[0]
        self.origin = frame[0], frame[1]

    def _call(self, method: str, args: GLib.Variant):
        return self.bus.call_sync(
            "org.gnome.Shell", "/io/github/jinwei/NestedInput", "io.github.jinwei.NestedInput",
            method, args, None, Gio.DBusCallFlags.NONE, 3000, None,
        ).unpack()  # fmt: skip

    def screen(self, bx: float, by: float) -> tuple[float, float]:
        wx, wy = self.editor.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, int(bx), int(by))
        ok, point = self.editor.compute_point(self.window, Graphene.Point().init(wx, wy))
        return self.origin[0] + point.x, self.origin[1] + point.y

    def char(self, line: int, col: int) -> tuple[float, float]:
        """Buffer point in the middle of a character."""
        it = self.editor.buffer.get_iter_at_line_offset(line, col)[1]
        rect = self.editor.get_iter_location(it)
        return rect.x + max(rect.width, 2) / 2, rect.y + rect.height / 2

    def click_widget(self, widget: Gtk.Widget, x: float | None = None, y: float | None = None):
        """Click a widget (its middle, or x, y in its own coordinates), also one in a
        popover, which is a surface of its own placed relative to the window's."""
        x = widget.get_width() / 2 if x is None else x
        y = widget.get_height() / 2 if y is None else y
        native = widget.get_native()
        ok, point = widget.compute_point(native, Graphene.Point().init(x, y))
        sx, sy = point.x, point.y
        if native is not self.window:
            nx, ny = native.get_surface_transform()
            wx, wy = self.window.get_surface_transform()
            surface = native.get_surface()
            sx += nx + surface.get_position_x() - wx
            sy += ny + surface.get_position_y() - wy
        self._call("Move", GLib.Variant("(dd)", (self.origin[0] + sx, self.origin[1] + sy)))
        pump(0.05)
        for state in (True, False):
            self._call("Button", GLib.Variant("(ub)", (1, state)))
            pump(0.03)
        pump(0.3)

    def move(self, bx: float, by: float) -> None:
        self._call("Move", GLib.Variant("(dd)", self.screen(bx, by)))
        pump(0.05)

    def click(self, bx: float, by: float, button: int = 1) -> None:
        self.move(bx, by)
        self._call("Button", GLib.Variant("(ub)", (button, True)))
        pump(0.03)
        self._call("Button", GLib.Variant("(ub)", (button, False)))
        pump(0.25)

    def key(self, keyval: int, pressed: bool | None = None) -> None:
        for state in (True, False) if pressed is None else (pressed,):
            self._call("Key", GLib.Variant("(ub)", (keyval, state)))
        pump(0.03)

    def type(self, text: str) -> None:
        for ch in text:
            if ch == "\n":
                self.key(Gdk.KEY_Return)
            elif ch == "*":
                self.key(Gdk.KEY_Shift_L, True)
                self.key(Gdk.KEY_asterisk)
                self.key(Gdk.KEY_Shift_L, False)
            else:
                self.key(Gdk.unicode_to_keyval(ord(ch)))
        pump(0.2)


def main() -> int:
    NOTES.mkdir(parents=True, exist_ok=True)
    (NOTES / "Test.md").write_text(NOTE)
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text(f'[notes]\nfolder = "{NOTES}"\n')
    app = Adw.Application(application_id="io.github.jinwei.LauncherNotesInputTest")
    app.register(None)
    window = NotesWindow(app)
    window.present()
    pump(1.0)
    window.open_note("Test.md")
    pump(0.5)
    editor = window.editor
    buffer = editor.buffer
    io = Input(window)

    def line(n: int) -> str:
        return editor.line_text(n)

    def cursor() -> tuple[int, int]:
        it = buffer.get_iter_at_mark(buffer.get_insert())
        return it.get_line(), it.get_line_offset()

    # Click on a letter of a bold word on another line (its ** are hidden while the
    # cursor is elsewhere), then type: the text goes exactly where it was clicked.
    io.click(*io.char(0, 5))
    col = line(3).index("bold") + 2  # between "bo" and "ld"
    x, y = io.char(3, col)
    io.click(x - 3, y)  # the left half of the "l"
    io.type("X")
    check("typing goes where the hidden-markup line was clicked", "**boXld**" in line(3),
          repr(line(3)))  # fmt: skip

    # Type on one line, click another, over and over, across every kind of line.
    for n, col in ((2, 2), (4, 3), (0, 4), (5, 3), (6, 8), (3, 2), (2, 30), (4, 12)):
        io.click(*io.char(n, min(col, len(line(n)))))
        io.type("ab")
    check("clicking between lines while typing never crashes", True)

    # Clicks past the end of lines whose ends are hidden (a link's address).
    for n in (2, 4, 3, 0, 6):
        _x, y = io.char(n, 0)
        io.click(editor._margin + 700, y)
        io.type("z")
        check(f"click past the end of line {n} puts the cursor there", cursor()[0] == n,
              repr(cursor()))  # fmt: skip

    # Hover links (the tooltip asks where the pointer is), then Ctrl+click one.
    io.click(*io.char(0, 3))
    opened = []
    editor.link_handler = opened.append
    link_col = line(2).index("docs") + 1
    for n, col in ((2, link_col), (6, line(6).index("https") + 4), (3, 6), (2, link_col + 1)):
        io.move(*io.char(n, col))
        pump(1.2)
    check("hovering links never crashes", True)
    io.key(Gdk.KEY_Control_L, True)
    io.click(*io.char(2, link_col))
    io.key(Gdk.KEY_Control_L, False)
    check("Ctrl+click opens the link", opened == ["https://example.com/very/long/path/here"],
          repr(opened))  # fmt: skip
    check("…without moving into the link", "docs" in line(2) and "](" in line(2))

    # A real click on a checkbox ticks it.
    pump(0.3)
    box = editor._checkboxes.get(5)
    check("checkbox drawn", box is not None)
    if box is not None:
        io.click(box.get_x() + box.get_width() / 2, box.get_y() + box.get_height() / 2)
        check("clicking the checkbox ticks it", line(5).startswith("- [x]"), repr(line(5)))

    # Arrow keys and Home around a hidden list marker.
    io.click(*io.char(4, 4))  # inside the item's text (earlier steps typed into it)
    io.key(Gdk.KEY_Home)
    pump(0.1)
    check("Home on a list item goes to its text", cursor() == (4, 2), repr(cursor()))
    io.key(Gdk.KEY_Left)
    pump(0.1)
    check("Left from there goes to the previous line", cursor() == (3, len(line(3))),
          repr(cursor()))  # fmt: skip
    io.key(Gdk.KEY_Right)
    pump(0.1)
    check("Right comes back to the item's text", cursor() == (4, 2), repr(cursor()))

    # Real typing of markdown.
    io.click(*io.char(6, len(line(6))))
    io.key(Gdk.KEY_End)
    io.type("\n- first\nsecond\n")
    check("typed '- ' makes a list that Enter continues",
          "\n- first\n- second\n- \n" in editor.text(), repr(editor.text()[-30:]))  # fmt: skip
    io.type("\nsome **bold** text")
    last = cursor()[0]  # the file's final empty line is still below
    check("typed **bold** is bold", "md-bold" in {
        t.get_property("name")
        for t in buffer.get_iter_at_line_offset(last, line(last).index("bold") + 1)[1].get_tags()
    }, repr(line(last)))  # fmt: skip

    # The outline: Ctrl+Shift+O lists the headings, the cursor's section selected; arrow
    # keys and Enter, or a click, jump to one and scroll it to the top.
    body = "\n".join(f"line {i}" for i in range(50))
    window.new_note(text=f"# Course\n{body}\n## Week **1**\n{body}\n## Week 2\n"
                         f"```\n# not a heading\n```\n### Detail\n{body}\n")  # fmt: skip
    pump(0.5)
    week1 = 51
    week2 = 102
    buffer.place_cursor(buffer.get_iter_at_line(week1 + 5)[1])
    pump(0.2)
    io.key(Gdk.KEY_Control_L, True)
    io.key(Gdk.KEY_Shift_L, True)
    io.key(Gdk.KEY_o)
    io.key(Gdk.KEY_Shift_L, False)
    io.key(Gdk.KEY_Control_L, False)
    pump(0.4)
    outline = window._outline
    rows = []
    while (row := outline.list.get_row_at_index(len(rows))) is not None:
        rows.append(row)
    labels = [r.get_child().get_label() for r in rows]
    check("Ctrl+Shift+O opens the outline", outline.get_visible())
    check("the outline lists the headings", labels == ["Course", "Week 1", "Week 2", "Detail"],
          repr(labels))  # fmt: skip
    selected = outline.list.get_selected_row()
    check("the cursor's section is selected", selected is rows[1] if rows else False)
    scroller = outline.get_child()
    check(
        "the outline fits without scrolling",
        scroller.get_vadjustment().get_upper() <= scroller.get_vadjustment().get_page_size() + 1,
    )
    io.key(Gdk.KEY_Down)
    io.key(Gdk.KEY_Return)
    pump(0.6)
    check("Enter on a heading jumps to it", cursor() == (week2, 3), repr(cursor()))
    check("…and closes the outline", not outline.get_visible())
    top = editor.get_visible_rect().y
    y = editor.get_iter_location(buffer.get_iter_at_line(week2)[1]).y
    check("…scrolled to the top", abs(y - top) < 30, f"heading at {y}, top {top}")
    io.type("Z")
    check("typing goes on in the editor", line(week2) == "## ZWeek 2", repr(line(week2)))
    io.click_widget(window._outline_button)
    pump(0.3)
    check("the header button opens it", outline.get_visible())
    if outline.get_visible():
        io.click_widget(outline.list.get_row_at_index(0))
        pump(0.6)
        check("clicking a heading jumps to it", cursor() == (0, 2), repr(cursor()))
        top = editor.get_visible_rect().y
        check("…scrolled back to the top", top < 30, repr(top))

    # PDF export: in the note's menu and on Ctrl+Shift+E; pictures are found next to
    # the note; a toast offers to open the file; failures are reported, not raised.
    menu = window._menu_button.get_menu_model()
    labels = [
        sub.get_item_attribute_value(i, "label").get_string()
        for section in range(menu.get_n_items())
        for sub in [menu.get_item_link(section, "section")]
        for i in range(sub.get_n_items())
    ]
    check("the menu offers Export to PDF…", "Export to PDF…" in labels, repr(labels))
    asked = []
    real_ask = window.ask_export_pdf
    window.ask_export_pdf = lambda: asked.append(True)
    io.key(Gdk.KEY_Control_L, True)
    io.key(Gdk.KEY_Shift_L, True)
    io.key(Gdk.KEY_e)
    io.key(Gdk.KEY_Shift_L, False)
    io.key(Gdk.KEY_Control_L, False)
    pump(0.3)
    window.ask_export_pdf = real_ask
    check("Ctrl+Shift+E asks where to export", asked == [True])
    pictures = NOTES / "attachments"
    pictures.mkdir(exist_ok=True)
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 300, 120)
    pixbuf.fill(0x2EC27EFF)
    pixbuf.savev(str(pictures / "shot.png"), "png", [], [])
    buffer.insert(buffer.get_end_iter(), "\n![](attachments/shot.png)\n")
    pump(0.3)
    exports = NOTES.parent / "exports"
    exports.mkdir(exist_ok=True)
    toasts = []
    real_add = window._toasts.add_toast
    window._toasts.add_toast = lambda t: (toasts.append(t), real_add(t))
    ok = window.export_pdf(exports / "Course.pdf")
    listing = subprocess.run(["pdfimages", "-list", str(exports / "Course.pdf")],
                             capture_output=True, text=True).stdout  # fmt: skip
    check("export writes the PDF", ok and (exports / "Course.pdf").stat().st_size > 1000)
    check("…with the note's picture", " 300   120 " in listing, listing)
    check("…and a toast that opens it",
          bool(toasts) and toasts[-1].get_button_label() == "Open", repr(toasts))  # fmt: skip
    check("…remembering the folder", window._state.get("export_folder") == str(exports))
    failed = window.export_pdf(exports / "missing" / "x.pdf")
    message = toasts[-1].get_title()
    check("a failed export is reported", not failed and "Could not export" in message, message)
    window._toasts.add_toast = real_add

    window.save_now()
    saved = (NOTES / window.session.rel).read_text()
    check("the note on disk is plain markdown", saved == editor.text())
    passed = sum(ok for _, ok in results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
