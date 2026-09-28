"""Export a note to PDF, looking like the editor.

A4 pages laid out with Pango and drawn with cairo, line by line like the editor shows
them: headings, lists with bullets, checkboxes and numbers, quotes with bars, code
blocks, rules, pictures and inline styles. Web links stay clickable and the headings
become the PDF's bookmarks. Needs no display, so it runs in the unit tests.
"""

from __future__ import annotations

import io
import os
from collections.abc import Callable
from pathlib import Path

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Pango, PangoCairo  # noqa: E402

from .. import notes_markdown as md  # noqa: E402

PAGE_WIDTH, PAGE_HEIGHT = 595.28, 841.89  # A4, in points
MARGIN = 56.7  # 2 cm
BODY_SIZE = 11.0
CODE_SIZE = 9.5
LEADING = 0.3  # extra space between lines, in body sizes
HEADING_SCALES = {1: 1.6, 2: 1.35, 3: 1.15}  # as in the editor
INDENT = 20.0  # per list level
QUOTE_STEP = 12.0  # per quote level
CODE_PAD = 8.0
MAX_IMAGE_HEIGHT = 420.0
MONOSPACE = "Monospace"
BULLETS = "•◦▪"

TEXT = (0.12, 0.12, 0.13)
DIM = (0.47, 0.47, 0.5)
SOFT = (0.3, 0.3, 0.33)
ACCENT = (0.11, 0.44, 0.85)
FAINT = (0.0, 0.0, 0.0, 0.06)  # code background
LINE = (0.75, 0.75, 0.77)  # rules, quote bars, empty checkboxes
HIGHLIGHT = (1.0, 0.91, 0.45)
WEB = ("http://", "https://", "mailto:")
MISSING_SUPPORT = "PDF export needs the python3-gi-cairo package: sudo apt install python3-gi-cairo"


def missing_support() -> str | None:
    """Why PDF export can't work here, or None. PyGObject hands cairo drawing contexts
    to Pango through a separate system package, which `apt autoremove` can remove."""
    try:
        gi.require_foreign("cairo")
    except ImportError:
        return MISSING_SUPPORT
    return None


def export_pdf(
    text: str,
    path: Path,
    *,
    title: str,
    image_path: Callable[[str], Path | None] = lambda _url: None,
    font: str = "Sans",
) -> int:
    """Write the note's markdown to `path` as a PDF. Returns the number of pages.
    Written next to it first, so a failed export leaves an existing file alone."""
    partial = path.with_name(f".{path.name}.part")
    try:
        writer = _Writer(partial, title, font, image_path)
        try:
            writer.write(text)
        finally:
            writer.close()
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return writer.page


def _rgb16(color: tuple[float, ...]) -> tuple[int, int, int]:
    return tuple(int(c * 65535) for c in color[:3])


def _attr(attr: Pango.Attribute, start: int, end: int) -> Pango.Attribute:
    attr.start_index, attr.end_index = start, end
    return attr


class _Writer:
    def __init__(
        self, path: Path, title: str, font: str, image_path: Callable[[str], Path | None]
    ) -> None:
        self.surface = cairo.PDFSurface(str(path), PAGE_WIDTH, PAGE_HEIGHT)
        self.surface.set_metadata(cairo.PDFMetadata.TITLE, title)
        self.surface.set_metadata(cairo.PDFMetadata.CREATOR, "Launcher Notes")
        self.cr = cairo.Context(self.surface)
        self.font = font
        self.image_path = image_path
        self.page = 1
        self.y = MARGIN
        self.left = MARGIN
        self.width = PAGE_WIDTH - 2 * MARGIN
        self.bottom = PAGE_HEIGHT - MARGIN
        self.body_line = self._line_height(BODY_SIZE)
        self.bookmarks: list[tuple[int, int]] = []  # (level, outline id) of open headings

    # --- pages ---------------------------------------------------------------------------

    def close(self) -> None:
        self._page_number()
        self.surface.finish()

    def new_page(self) -> None:
        self._page_number()
        self.cr.show_page()
        self.page += 1
        self.y = MARGIN

    def room_for(self, height: float) -> None:
        """Start a new page unless `height` fits on this one (or the page is empty)."""
        if self.y + height > self.bottom and self.y > MARGIN:
            self.new_page()

    def _page_number(self) -> None:
        layout = self.layout(str(self.page), size=8.5, color=DIM)
        _ink, logical = layout.get_pixel_extents()
        self.cr.move_to((PAGE_WIDTH - logical.width) / 2, PAGE_HEIGHT - MARGIN / 2 - 6)
        PangoCairo.show_layout(self.cr, layout)

    # --- text ----------------------------------------------------------------------------

    def _line_height(self, size: float) -> float:
        layout = self.layout("Xg", size=size)  # kept alive while its line is used
        _ink, logical = layout.get_line_readonly(0).get_pixel_extents()
        return logical.height + LEADING * size

    def layout(
        self,
        text: str,
        styles: list[md.Span] = (),
        *,
        size: float = BODY_SIZE,
        width: float | None = None,
        bold: bool = False,
        italic: bool = False,
        mono: bool = False,
        color: tuple[float, ...] = TEXT,
        whole: list[Pango.Attribute] = (),
    ) -> Pango.Layout:
        layout = PangoCairo.create_layout(self.cr)
        desc = Pango.FontDescription.from_string(MONOSPACE if mono else self.font)
        desc.set_size(int(size * Pango.SCALE))
        if bold:
            desc.set_weight(Pango.Weight.BOLD)
        if italic:
            desc.set_style(Pango.Style.ITALIC)
        layout.set_font_description(desc)
        if width is not None:
            layout.set_width(int(width * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_text(text, -1)
        attrs = Pango.AttrList()
        end = len(text.encode())
        attrs.insert(_attr(Pango.attr_foreground_new(*_rgb16(color)), 0, end))
        for attr in whole:
            attrs.insert(_attr(attr, 0, end))
        for style in styles:
            a = len(text[: style.start].encode())
            z = len(text[: style.end].encode())
            for attr in self._style_attrs(style.kind, size):
                attrs.insert(_attr(attr, a, z))
        layout.set_attributes(attrs)
        return layout

    @staticmethod
    def _style_attrs(kind: str, size: float) -> list[Pango.Attribute]:
        if kind == "bold":
            return [Pango.attr_weight_new(Pango.Weight.BOLD)]
        if kind == "italic":
            return [Pango.attr_style_new(Pango.Style.ITALIC)]
        if kind == "strike":
            return [Pango.attr_strikethrough_new(True)]
        if kind == "underline":
            return [Pango.attr_underline_new(Pango.Underline.SINGLE)]
        if kind == "highlight":
            return [Pango.attr_background_new(*_rgb16(HIGHLIGHT))]
        if kind == "code":
            return [
                Pango.attr_family_new(MONOSPACE),
                Pango.attr_size_new(int(size * 0.9 * Pango.SCALE)),
                Pango.attr_background_new(*_rgb16((0.93, 0.93, 0.94))),
            ]
        if kind in ("link", "url"):
            return [
                Pango.attr_foreground_new(*_rgb16(ACCENT)),
                Pango.attr_underline_new(Pango.Underline.SINGLE),
            ]
        return []

    def flow(
        self,
        layout: Pango.Layout,
        x: float,
        *,
        links: list[md.Span] = (),
        text: str = "",
        size: float = BODY_SIZE,
        decorate: Callable[[bool, float, float, float], None] | None = None,
    ) -> float | None:
        """Draw a layout at x from the current y, one line at a time so a long paragraph
        breaks across pages. `decorate(first, top, baseline, height)` draws what goes
        with each line (bullet, quote bars, code background). Returns the first line's
        baseline."""
        first_baseline = None
        it = layout.get_iter()
        while True:
            line = it.get_line_readonly()
            _ink, logical = it.get_line_extents()
            top_in_layout = logical.y / Pango.SCALE
            height = logical.height / Pango.SCALE + LEADING * size
            self.room_for(height)
            baseline = self.y + it.get_baseline() / Pango.SCALE - top_in_layout
            if decorate is not None:
                decorate(first_baseline is None, self.y, baseline, height)
            if first_baseline is None:
                first_baseline = baseline
            self.cr.move_to(x + logical.x / Pango.SCALE, baseline)
            PangoCairo.show_layout_line(self.cr, line)
            self._link_areas(line, x, self.y, height, links, text)
            self.y += height
            if not it.next_line():
                return first_baseline

    def _link_areas(self, line, x: float, top: float, height: float, links, text: str) -> None:
        start = line.get_start_index()
        end = start + line.get_length()
        for link in links:
            if not link.url.startswith(WEB):
                continue  # notes and files only open from Notes itself
            a = max(len(text[: link.start].encode()), start)
            z = min(len(text[: link.end].encode()), end)
            if a >= z:
                continue
            x1 = x + line.index_to_x(a, False) / Pango.SCALE
            x2 = x + line.index_to_x(z, False) / Pango.SCALE
            uri = link.url.replace("\\", "\\\\").replace("'", "\\'")
            rect = f"{min(x1, x2):.2f} {top:.2f} {abs(x2 - x1):.2f} {height:.2f}"
            self.cr.tag_begin(cairo.TAG_LINK, f"rect=[{rect}] uri='{uri}'")
            self.cr.tag_end(cairo.TAG_LINK)

    # --- blocks --------------------------------------------------------------------------

    def write(self, text: str) -> None:
        lines = text.split("\n")
        infos = md.classify(lines)
        self.levels = md.ordered_levels(infos)  # 1. / a) / i. as in the editor
        for n, (line, info) in enumerate(zip(lines, infos, strict=True)):
            draw = getattr(self, f"_{info.kind}", self._text)
            draw(line, info, lines, infos, n)

    def _blank(self, line, info, lines, infos, n) -> None:
        self.y += self.body_line * 0.6

    def _text(self, line, info, lines, infos, n, *, start: int = 0) -> None:
        shown, styles = md.styled_text(line, start)
        layout = self.layout(shown, styles, width=self.width)
        self.flow(layout, self.left, links=styles, text=shown)

    def _heading(self, line, info, lines, infos, n) -> None:
        size = BODY_SIZE * HEADING_SCALES.get(info.level, HEADING_SCALES[3])
        shown, styles = md.styled_text(line, info.content)
        layout = self.layout(shown, styles, size=size, width=self.width, bold=True)
        if self.y > MARGIN:
            self.y += size * 0.7
        # Keep a heading with (at least two lines of) what follows it.
        self.room_for(self._line_height(size) + 2 * self.body_line)
        self._bookmark(info.level, shown or " ")
        self.flow(layout, self.left, links=styles, text=shown, size=size)
        self.y += size * 0.2

    def _bookmark(self, level: int, title: str) -> None:
        while self.bookmarks and self.bookmarks[-1][0] >= level:
            self.bookmarks.pop()
        parent = self.bookmarks[-1][1] if self.bookmarks else cairo.PDF_OUTLINE_ROOT
        target = f"page={self.page} pos=[{self.left:.2f} {self.y:.2f}]"
        outline = self.surface.add_outline(parent, title, target, cairo.PDFOutlineFlags.OPEN)
        self.bookmarks.append((level, outline))

    def _item_layout(self, line, info, x: float, **kwargs) -> tuple[Pango.Layout, str, list]:
        shown, styles = md.styled_text(line, info.content)
        layout = self.layout(shown, styles, width=self.left + self.width - x, **kwargs)
        return layout, shown, styles

    def _bullet(self, line, info, lines, infos, n) -> None:
        x = self.left + INDENT * (info.depth + 1)
        layout, shown, styles = self._item_layout(line, info, x)
        glyph = BULLETS[info.depth % len(BULLETS)]

        def bullet(first, _top, baseline, _height) -> None:
            if first:
                self._glyph(glyph, x - INDENT / 2, baseline)

        self.flow(layout, x, links=styles, text=shown, decorate=bullet)

    def _task(self, line, info, lines, infos, n) -> None:
        x = self.left + INDENT * (info.depth + 1)
        whole = [Pango.attr_strikethrough_new(True)] if info.checked else []
        layout, shown, styles = self._item_layout(
            line, info, x, color=DIM if info.checked else TEXT, whole=whole
        )

        def checkbox(first, _top, baseline, _height) -> None:
            if first:
                self._checkbox(x - INDENT / 2, baseline, info.checked)

        self.flow(layout, x, links=styles, text=shown, decorate=checkbox)

    def _ordered(self, line, info, lines, infos, n) -> None:
        x = self.left + INDENT * (info.depth + 1)
        layout, shown, styles = self._item_layout(line, info, x)
        number = md.list_label(info.number, self.levels[n], info.delim)

        def draw_number(first, _top, baseline, _height) -> None:
            if first:
                label = self.layout(number, bold=True)
                _ink, logical = label.get_pixel_extents()
                ascent = label.get_baseline() / Pango.SCALE
                self.cr.move_to(x - 5 - logical.width, baseline - ascent)
                PangoCairo.show_layout(self.cr, label)

        self.flow(layout, x, links=styles, text=shown, decorate=draw_number)

    def _quote(self, line, info, lines, infos, n) -> None:
        x = self.left + QUOTE_STEP * info.depth + 4
        layout, shown, styles = self._item_layout(line, info, x, italic=True, color=SOFT)

        def bars(_first, top, _baseline, height) -> None:
            self.cr.set_source_rgb(*LINE)
            for d in range(info.depth):
                self.cr.rectangle(self.left + QUOTE_STEP * d + 1, top, 3, height)
            self.cr.fill()

        self.flow(layout, x, links=styles, text=shown, decorate=bars)

    def _rule(self, line, info, lines, infos, n) -> None:
        self.room_for(self.body_line)
        middle = self.y + self.body_line / 2
        self.cr.set_source_rgb(*LINE)
        self.cr.rectangle(self.left, middle - 0.5, self.width, 1)
        self.cr.fill()
        self.y += self.body_line

    def _fence(self, line, info, lines, infos, n) -> None:
        # Fences aren't printed: they open and close the code block's background.
        opening = n + 1 < len(infos) and infos[n + 1].kind == "code"
        self.y += CODE_PAD / 2 if opening else CODE_PAD

    def _code(self, line, info, lines, infos, n) -> None:
        width = self.width - 2 * CODE_PAD
        layout = self.layout(line or " ", size=CODE_SIZE, width=width, mono=True)
        first_line = n == 0 or infos[n - 1].kind != "code"
        last_line = n + 1 >= len(infos) or infos[n + 1].kind != "code"

        def background(first, top, _baseline, height) -> None:
            pad_top = CODE_PAD / 2 if first and first_line else 0
            self.cr.set_source_rgba(*FAINT)
            self.cr.rectangle(self.left, top - pad_top, self.width, height + pad_top)
            self.cr.fill()

        self.flow(layout, self.left + CODE_PAD, size=CODE_SIZE, decorate=background)
        if last_line:
            self.cr.set_source_rgba(*FAINT)
            self.cr.rectangle(self.left, self.y, self.width, CODE_PAD / 2)
            self.cr.fill()
            self.y += CODE_PAD / 2

    def _image(self, line, info, lines, infos, n) -> None:
        path = self.image_path(info.url)
        pixbuf = None
        if path is not None:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
            except GLib.Error:
                pixbuf = None
        if pixbuf is None:  # missing, or on the web: the link itself, dimmed
            self.flow(self.layout(line.strip(), width=self.width, color=DIM), self.left)
            return
        scale = min(1.0, self.width / pixbuf.get_width())
        scale = min(scale, MAX_IMAGE_HEIGHT / pixbuf.get_height())
        height = pixbuf.get_height() * scale
        self.y += 4
        self.room_for(height)
        _ok, png = pixbuf.save_to_bufferv("png", [], [])
        surface = cairo.ImageSurface.create_from_png(io.BytesIO(png))
        self.cr.save()
        self.cr.translate(self.left, self.y)
        self.cr.scale(scale, scale)
        self.cr.set_source_surface(surface, 0, 0)
        self.cr.paint()
        self.cr.restore()
        self.y += height + 8

    # --- drawn marks ---------------------------------------------------------------------

    def _glyph(self, glyph: str, cx: float, baseline: float) -> None:
        layout = self.layout(glyph, color=SOFT)
        _ink, logical = layout.get_pixel_extents()
        ascent = layout.get_baseline() / Pango.SCALE
        self.cr.move_to(cx - logical.width / 2, baseline - ascent)
        PangoCairo.show_layout(self.cr, layout)

    def _checkbox(self, cx: float, baseline: float, checked: bool) -> None:
        side = BODY_SIZE * 0.78
        cap = BODY_SIZE * 0.7
        top = baseline - cap / 2 - side / 2
        left = cx - side / 2
        _rounded(self.cr, left, top, side, side, 2)
        if checked:
            self.cr.set_source_rgb(*ACCENT)
            self.cr.fill()
            self.cr.set_source_rgb(1, 1, 1)
            self.cr.set_line_width(1.4)
            self.cr.move_to(left + side * 0.22, top + side * 0.52)
            self.cr.line_to(left + side * 0.42, top + side * 0.72)
            self.cr.line_to(left + side * 0.78, top + side * 0.3)
            self.cr.stroke()
        else:
            self.cr.set_source_rgb(*DIM)
            self.cr.set_line_width(1)
            self.cr.stroke()


def _rounded(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.14159)
    cr.arc(x + r, y + r, r, 3.14159, 4.71239)
    cr.close_path()
