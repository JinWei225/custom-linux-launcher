"""Markdown block structure for the Notes editor, line by line.

Pure Python (no GTK) so it can be unit tested. The editor keeps the note as plain
markdown text; this module says what each line is (heading, list item, quote, code...)
and works out edits: what Enter continues, indenting, removing a marker, toggling a
checkbox, list renumbering.

Kinds and which leading characters are markup:
  heading  "## Title"      marker "## " (shown dim on the cursor's line, hidden elsewhere)
  bullet   "\\t- item"     hidden "\\t- " (a bullet is drawn instead)
  task     "- [ ] item"    hidden "- [ ] " (a checkbox is drawn instead)
  ordered  "\\t1. item"    hidden "\\t"; "1. " stays visible, styled
  quote    "> text"        hidden "> " (a bar is drawn instead); needs the space
  rule     "---"           the whole line (a line is drawn instead)
  fence    "```python"     shown dim; lines between two fences are "code"
  image    "![](a.png)"    a line holding only an image: drawn below the line

Inline styles inside a line (inline_spans): **bold**, *italic* / _italic_, ~~strike~~,
`code`, ==highlight==, <u>underline</u>, [links](url) and bare https:// URLs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ATTACHMENTS = "attachments"  # pictures pasted into a note go in this folder next to it
INDENT = "\t"  # what Tab adds in front of a list item (Obsidian does the same)
MAX_DEPTH = 8

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_RULE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+")
_TASK = re.compile(r"^([ \t]*)([-*+])[ \t]+\[([ xX])\](?:[ \t]+|$)")
_BULLET = re.compile(r"^([ \t]*)([-*+])[ \t]+")
_ORDERED = re.compile(r"^([ \t]*)(\d{1,9})([.)])[ \t]+")
_QUOTE = re.compile(r"^ {0,3}((?:>[ \t]?)+)")
# "[] ", "[ ] ", "- [] " typed at the start of a line: a checkbox.
_TASK_SHORTHAND = re.compile(r"^([ \t]*)(?:[-*+][ \t]+)?\[ ?\][ \t]")

LIST_KINDS = ("bullet", "task", "ordered")
_DESTINATION = r"(<[^<>\n]*>|[^\s()<>]+)"  # a link target: plain, or <with spaces>
_IMAGE_LINE = re.compile(r"^\s*!\[[^\]\n]*\]\(" + _DESTINATION + r"\)\s*$")


@dataclass(frozen=True)
class LineInfo:
    kind: str  # blank text heading bullet task ordered quote rule fence code
    depth: int = 0  # list nesting (0 = top) or quote depth (1 = ">")
    level: int = 0  # heading level
    hidden: int = 0  # leading characters always hidden (indent + bullet/checkbox/quote)
    marker: int = 0  # leading characters that are markup (heading, ordered number, rule)
    content: int = 0  # column where the text itself starts
    number: int = 0  # ordered lists
    delim: str = ""  # "." or ")" (ordered), "-" "*" "+" (bullet/task)
    checked: bool = False
    indent: str = ""  # the leading whitespace of a list item
    url: str = ""  # image lines: the image's path or URL


def classify(lines: list[str]) -> list[LineInfo]:
    """What every line is. Needs all lines: code fences and list nesting span lines."""
    infos: list[LineInfo] = []
    fence: str | None = None  # the open fence ("```"), if inside a code block
    stack: list[int] = []  # indent widths of the enclosing list items
    for line in lines:
        if fence is not None:
            m = _FENCE.match(line)
            if (
                m
                and m.group(1)[0] == fence[0]
                and len(m.group(1)) >= len(fence)
                and (not m.group(2).strip())
            ):
                infos.append(LineInfo("fence", marker=len(line)))
                fence = None
            else:
                infos.append(LineInfo("code"))
            continue
        info = _classify_line(line)
        if info.kind == "text" and line.strip() == ">" and infos and infos[-1].kind == "quote":
            # A bare ">" continues a quote (files often have them between paragraphs),
            # but on its own it waits for the space: typing ">" alone changes nothing.
            info = LineInfo("quote", depth=1, hidden=len(line), content=len(line))
        if info.kind == "fence":
            fence = _FENCE.match(line).group(1)
        if info.kind in LIST_KINDS:
            width = _width(info.indent)
            while stack and width < stack[-1]:
                stack.pop()
            if not stack or width > stack[-1]:
                stack.append(width)
            depth = min(len(stack) - 1, MAX_DEPTH)
            info = LineInfo(**{**info.__dict__, "depth": depth})
        elif info.kind != "blank":
            stack.clear()
        infos.append(info)
    return infos


def _width(indent: str) -> int:
    return sum(4 if ch == "\t" else 1 for ch in indent)


def _classify_line(line: str) -> LineInfo:
    if not line.strip():
        return LineInfo("blank")
    if m := _IMAGE_LINE.match(line):
        return LineInfo("image", marker=len(line), url=_destination(m.group(1)))
    if m := _FENCE.match(line):
        if "`" not in m.group(2) or m.group(1)[0] == "~":
            return LineInfo("fence", marker=len(line))
    if _RULE.match(line):
        return LineInfo("rule", marker=len(line))
    if m := _HEADING.match(line):
        return LineInfo("heading", level=len(m.group(1)), marker=m.end(), content=m.end())
    if m := _TASK.match(line):
        return LineInfo(
            "task", hidden=m.end(), content=m.end(), delim=m.group(2),
            checked=m.group(3) in "xX", indent=m.group(1),
        )  # fmt: skip
    if m := _BULLET.match(line):
        return LineInfo(
            "bullet", hidden=m.end(), content=m.end(), delim=m.group(2), indent=m.group(1)
        )
    if m := _ORDERED.match(line):
        return LineInfo(
            "ordered", hidden=len(m.group(1)), marker=m.end(), content=m.end(),
            number=int(m.group(2)), delim=m.group(3), indent=m.group(1),
        )  # fmt: skip
    if m := _QUOTE.match(line):
        prefix = m.group(1)
        if prefix.endswith((" ", "\t")):  # ">" becomes a quote once the space is typed
            return LineInfo("quote", depth=prefix.count(">"), hidden=m.end(), content=m.end())
    return LineInfo("text")


def is_empty_item(line: str, info: LineInfo) -> bool:
    """A list item or quote line with nothing after its marker."""
    return info.kind in (*LIST_KINDS, "quote") and not line[info.content :].strip()


def continuation(line: str, info: LineInfo) -> str | None:
    """What Enter starts the next line with, or None for a plain newline."""
    if info.kind == "bullet":
        return f"{info.indent}{info.delim} "
    if info.kind == "task":
        return f"{info.indent}{info.delim} [ ] "
    if info.kind == "ordered":
        return f"{info.indent}{info.number + 1}{info.delim} "
    if info.kind == "quote":
        return "> " * info.depth
    return None


def indent(line: str) -> str:
    return INDENT + line


def outdent(line: str) -> str:
    """Remove one level of indentation: a tab, or up to 4 spaces."""
    if line.startswith("\t"):
        return line[1:]
    spaces = len(line) - len(line.lstrip(" "))
    return line[min(spaces, 4) :]


def without_marker(line: str, info: LineInfo) -> str:
    """The line as plain text: "- item" -> "item", "## Title" -> "Title"."""
    if info.kind in (*LIST_KINDS, "quote", "heading"):
        return line[info.content :]
    return line


def toggle_task(line: str, info: LineInfo) -> str:
    """ "- [ ] x" <-> "- [x] x" """
    if info.kind != "task":
        return line
    box = line.index("[", len(info.indent))
    return line[: box + 1] + (" " if info.checked else "x") + line[box + 2 :]


def task_shorthand(line: str) -> tuple[int, str] | None:
    """ "[] " / "[ ] " / "- [] " typed at a line start -> (length replaced, "- [ ] ")."""
    m = _TASK_SHORTHAND.match(line)
    if m is None or _TASK.match(line):
        return None
    return m.end(), f"{m.group(1)}- [ ] "


def renumber(lines: list[str], infos: list[LineInfo]) -> list[tuple[int, int, int, str]]:
    """Edits that number each ordered list 1, 2, 3... from its first item's number.

    A list runs over items of the same depth; deeper items and blank lines in between
    don't break it. Returns (line, start column, end column, new number)."""
    if len(lines) != len(infos):
        raise ValueError("lines and infos differ in length")
    edits = []
    counters: dict[int, int] = {}  # depth -> next number
    for i, info in enumerate(infos):
        if info.kind == "ordered":
            for depth in [d for d in counters if d > info.depth]:
                del counters[depth]
            expected = counters.get(info.depth, info.number)
            if info.number != expected:
                start = len(info.indent)
                edits.append((i, start, start + len(str(info.number)), str(expected)))
            counters[info.depth] = expected + 1
        elif info.kind in ("bullet", "task"):
            for depth in [d for d in counters if d >= info.depth]:
                del counters[depth]
        elif info.kind != "blank":
            counters.clear()
    return edits


# --- inline styles --------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """An inline style in a line. start..end includes the markers; inner_start..
    inner_end is the text shown. Columns are within the line."""

    kind: str  # bold italic strike code highlight underline link image url
    start: int
    end: int
    inner_start: int
    inner_end: int
    url: str = ""


_CODE_SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(" + _DESTINATION + r"(?:\s+\"[^\"\n]*\")?\)")
_ANGLE_URL = re.compile(r"<((?:https?|mailto):[^<>\s]+)>")
_BARE_URL = re.compile(r"(?<![\w/])(?:https?://|www\.)[^\s<>]+")
_UNDERLINE = re.compile(r"<u>(.+?)</u>")
_BOLD_ITALIC = re.compile(r"(?<![*\\])\*\*\*(?=[^\s*])(.+?)(?<=[^\s*])\*\*\*(?!\*)")
_PAIRED = (
    ("bold", re.compile(r"(?<!\\)\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)")),
    ("bold", re.compile(r"(?<![\w\\])__(?=\S)(.+?)(?<=\S)__(?!\w)")),
    ("strike", re.compile(r"(?<!\\)~~(?=\S)(.+?)(?<=\S)~~")),
    ("highlight", re.compile(r"(?<!\\)==(?=\S)(.+?)(?<=\S)==")),
)
_ITALIC = (
    re.compile(r"(?<![*\\])\*(?=[^\s*])(.+?)(?<=[^\s*\\])\*(?!\*)"),
    re.compile(r"(?<![\w\\])_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)"),
)
_BLOCKED = "\x00"  # stands in for text another span owns
_USED = "\x01"  # stands in for markers already matched


def _destination(raw: str) -> str:
    return raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw


def inline_spans(line: str, start: int = 0) -> list[Span]:
    """The inline styles in a line, from column `start` (after a list marker...)."""
    spans: list[Span] = []
    work = list(_BLOCKED * start + line[start:])

    def text() -> str:
        return "".join(work)

    def block(a: int, z: int, fill: str = _BLOCKED) -> None:
        work[a:z] = fill * (z - a)

    # Code spans and links first: nothing inside them is styled.
    for m in _CODE_SPAN.finditer(text()):
        n = len(m.group(1))
        spans.append(Span("code", m.start(), m.end(), m.start() + n, m.end() - n))
        block(m.start(), m.end())
    for m in _LINK.finditer(text()):
        kind = "image" if m.group(1) else "link"
        inner_start = m.start() + len(m.group(1)) + 1
        url = _destination(m.group(3))
        spans.append(
            Span(kind, m.start(), m.end(), inner_start, inner_start + len(m.group(2)), url)
        )
        # The link text can still be bold or italic; the rest of the link can't.
        block(m.start(), inner_start)
        block(inner_start + len(m.group(2)), m.end())
    for m in _ANGLE_URL.finditer(text()):
        spans.append(Span("url", m.start(), m.end(), m.start() + 1, m.end() - 1, m.group(1)))
        block(m.start(), m.end())
    for m in _BARE_URL.finditer(text()):
        url = m.group(0).rstrip(".,;:!?'\")")
        if url.count("(") < m.group(0).count(")") and url.endswith(")"):
            url = url[:-1]
        end = m.start() + len(url)
        full = url if url.startswith("http") else "https://" + url
        spans.append(Span("url", m.start(), end, m.start(), end, full))
        block(m.start(), end)
    for m in _UNDERLINE.finditer(text()):
        spans.append(Span("underline", m.start(), m.end(), m.start() + 3, m.end() - 4))
        block(m.start(), m.start() + 3, _USED)
        block(m.end() - 4, m.end(), _USED)
    for m in _BOLD_ITALIC.finditer(text()):
        spans.append(Span("bold", m.start(), m.end(), m.start() + 3, m.end() - 3))
        spans.append(Span("italic", m.start() + 3, m.end() - 3, m.start() + 3, m.end() - 3))
        block(m.start(), m.start() + 3, _USED)
        block(m.end() - 3, m.end(), _USED)
    for kind, pattern in _PAIRED:
        for m in pattern.finditer(text()):
            spans.append(Span(kind, m.start(), m.end(), m.start() + 2, m.end() - 2))
            block(m.start(), m.start() + 2, _USED)
            block(m.end() - 2, m.end(), _USED)
    for pattern in _ITALIC:
        for m in pattern.finditer(text()):
            spans.append(Span("italic", m.start(), m.end(), m.start() + 1, m.end() - 1))
            block(m.start(), m.start() + 1, _USED)
            block(m.end() - 1, m.end(), _USED)
    return sorted(spans, key=lambda s: (s.start, -s.end))


MARKERS = {
    "bold": ("**", "**"),
    "italic": ("*", "*"),
    "strike": ("~~", "~~"),
    "code": ("`", "`"),
    "highlight": ("==", "=="),
    "underline": ("<u>", "</u>"),
}


def toggle_wrap(text: str, sel_start: int, sel_end: int, kind: str) -> tuple[str, int, int]:
    """Wrap text[sel_start:sel_end] in a style's markers, or unwrap it if it already is.
    Returns the new text and the new selection. An empty selection gets empty markers
    with the cursor between them."""
    opening, closing = MARKERS[kind]
    before, inner, after = text[:sel_start], text[sel_start:sel_end], text[sel_end:]
    if (
        before.endswith(opening)
        and after.startswith(closing)
        and (kind != "italic" or not (before.endswith("**") and after.startswith("**")))
    ):
        new = before[: -len(opening)] + inner + after[len(closing) :]
        return new, sel_start - len(opening), sel_end - len(opening)
    if inner.startswith(opening) and inner.endswith(closing) and len(inner) >= len(opening) * 2:
        stripped = inner[len(opening) : len(inner) - len(closing)]
        return before + stripped + after, sel_start, sel_start + len(stripped)
    new = before + opening + inner + closing + after
    return new, sel_start + len(opening), sel_end + len(opening)


def attachment_name(note_stem: str, stamp: str, taken: set[str]) -> str:
    """ "Lecture 3", "20260928-121314" -> "Lecture-3-20260928-121314.png" (made unique)."""
    slug = re.sub(r"[^\w-]+", "-", note_stem, flags=re.UNICODE).strip("-") or "image"
    name, n = f"{slug}-{stamp}.png", 1
    while name.casefold() in {t.casefold() for t in taken}:
        n += 1
        name = f"{slug}-{stamp}-{n}.png"
    return name


def attachment_links(text: str) -> list[str]:
    """Paths of the note's own attachments it links to ("attachments/x.png")."""
    found = []
    for m in _LINK.finditer(text):
        url = _destination(m.group(3))
        if url.startswith("attachments/") and "/" not in url[len("attachments/") :]:
            found.append(url)
    return found
