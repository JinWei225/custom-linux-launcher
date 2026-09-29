"""Markdown block structure for the Notes editor, line by line.

Pure Python (no GTK) so it can be unit tested. The editor keeps the note as plain
markdown text; this module says what each line is (heading, list item, quote, code...)
and works out edits: what Enter continues, indenting, removing a marker, toggling a
checkbox, list renumbering.

Kinds and which leading characters are markup:
  heading  "## Title"      marker "## " (shown dim on the cursor's line, hidden elsewhere)
  bullet   "\\t- item"     hidden "\\t- " (a bullet is drawn instead)
  task     "- [ ] item"    hidden "- [ ] " (a checkbox is drawn instead)
  ordered  "\\t1. item"    hidden "\\t", marker "\\t1. " (the number is drawn in the margin, as
                           written; the editor shows it as text while it is being edited)

Numbered lists nested in numbered lists are lettered, then roman, as the file says:
"1. top", "\\ta) one in", "\\t\\ti. two in", then numbers again. Letters and roman numerals
only count where they belong (a) one list in, i. two in); anywhere else, as in
"a) first point", they are plain text. Tab / Shift+Tab give an item its new level's
marker; Enter continues an item's own.
  quote    "> text"        hidden "> " (a bar is drawn instead); needs the space
  rule     "---"           the whole line (a line is drawn instead)
  fence    "```python"     shown dim; lines between two fences are "code"
  image    "![](a.png)"    a line holding only an image: drawn below the line

Inline styles inside a line (inline_spans): **bold**, *italic* / _italic_, ~~strike~~,
`code`, ==highlight==, <u>underline</u>, [links](url) and bare https:// URLs.
"""

from __future__ import annotations

import dataclasses
import functools
import re
from collections.abc import Callable
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
# A lettered or roman item ("\tb) ", "\t\tiv. "): which one, and whether it is an item at
# all, depends on the lists around it (see classify).
_ALPHA_ORDERED = re.compile(r"^([ \t]+)([a-z]{1,15})([.)])[ \t]+")
_QUOTE = re.compile(r"^ {0,3}((?:>[ \t]?)+)")
# "[] ", "[ ] ", "- [] " typed at the start of a line: a checkbox.
_TASK_SHORTHAND = re.compile(r"^([ \t]*)(?:[-*+][ \t]+)?\[ ?\][ \t]")
# Another list marker typed on an empty item switches its type: "1. - " -> "- ". Only
# "1.", "a)" and "i." start a numbered list, so "- 2024. was a good year" stays a bullet.
_RETYPE = re.compile(
    r"^([ \t]*)(\d{1,9}[.)]|[a-z]{1,15}[.)]|[-*+])[ \t]+(1[.)]|[ai][.)]|[-*+])[ \t]$"
)

LIST_KINDS = ("bullet", "task", "ordered")
_DESTINATION = r"(<[^<>\n]*>|[^\s()<>]+)"  # a link target: plain, or <with spaces>
_IMAGE_LINE = re.compile(r"^\s*!\[[^\]\n]*\]\(" + _DESTINATION + r"\)\s*$")


@dataclass(frozen=True)
class LineInfo:
    kind: str  # blank text heading bullet task ordered quote rule fence code
    depth: int = 0  # list nesting (0 = top) or quote depth (1 = ">")
    level: int = 0  # heading level
    hidden: int = 0  # leading characters always hidden (indent, bullet/checkbox/quote)
    marker: int = 0  # leading characters that are markup (heading, ordered number, rule)
    content: int = 0  # column where the text itself starts
    number: int = 0  # ordered lists
    style: str = ""  # ordered lists: "1" (digits), "a" (letters) or "i" (roman numerals)
    delim: str = ""  # "." or ")" (ordered), "-" "*" "+" (bullet/task)
    checked: bool = False
    indent: str = ""  # the leading whitespace of a list item
    url: str = ""  # image lines: the image's path or URL


def classify(lines: list[str]) -> list[LineInfo]:
    """What every line is. Needs all lines: code fences and list nesting span lines."""
    infos: list[LineInfo] = []
    fence: str | None = None  # the open fence ("```"), if inside a code block
    # The enclosing list items: (indent width, numbered lists up to it, style, number).
    stack: list[tuple[int, int, str, int]] = []
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
        if info.kind == "text":
            info = _lettered(line, stack) or info
        if info.kind in LIST_KINDS:
            width = _width(info.indent)
            while stack and width < stack[-1][0]:
                stack.pop()
            if stack and stack[-1][0] == width:
                stack.pop()  # the item before, at the same level
            level = stack[-1][1] if stack else 0
            info = _at_depth(info, min(len(stack), MAX_DEPTH))
            stack.append((width, level + (info.kind == "ordered"), info.style, info.number))
        elif info.kind != "blank":
            stack.clear()
        infos.append(info)
    return infos


def _lettered(line: str, stack: list[tuple[int, int, str, int]]) -> LineInfo | None:
    """ "\\tb) text" as an item, if it is one where it stands: letters one numbered list
    in (then every third level), roman numerals two in. Longer ones ("aa)", "xl.") only
    count right after an item numbered one less (or the same), so a line like
    "\\tvs. that" isn't taken for one."""
    m = _ALPHA_ORDERED.match(line)
    if m is None:
        return None
    width = _width(m.group(1))
    parents = [entry for entry in stack if entry[0] < width]
    level = parents[-1][1] if parents else 0
    before = next(((e[2], e[3]) for e in stack if e[0] == width), ("", 0))
    token = m.group(2)
    if level % 3 == 1:
        style, number, short = "a", _letters_value(token), len(token) == 1
    elif level % 3 == 2:
        style, number = "i", _roman_value(token)
        short = number is not None and number <= 39
    else:
        return None
    # Same as the one before too: Enter just inserted an item there, before renumbering.
    if number is None or not (short or before in ((style, number - 1), (style, number))):
        return None
    return LineInfo(
        "ordered", hidden=m.end(1), marker=m.end(), content=m.end(), number=number,
        style=style, delim=m.group(3), indent=m.group(1),
    )  # fmt: skip


@functools.lru_cache(maxsize=256)
def _width(indent: str) -> int:
    return sum(4 if ch == "\t" else 1 for ch in indent)


@functools.lru_cache(maxsize=32768)
def _at_depth(info: LineInfo, depth: int) -> LineInfo:
    return info if info.depth == depth else dataclasses.replace(info, depth=depth)


# The editor classifies the whole note on every keystroke; lines seldom change, so the
# per-line work (a dozen regexes) is cached. LineInfo is frozen, so sharing is safe.
@functools.lru_cache(maxsize=32768)
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
            "ordered", hidden=m.end(1), marker=m.end(), content=m.end(),
            number=int(m.group(2)), style="1", delim=m.group(3), indent=m.group(1),
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
        return f"{info.indent}{format_number(info.number + 1, info.style)}{info.delim} "
    if info.kind == "quote":
        return "> " * info.depth
    return None


def indent(line: str) -> str:
    return INDENT + line


def reindent(lines: list[str], n: int, change: Callable[[str], str]) -> str:
    """Line n after Tab (change=indent) or Shift+Tab (change=outdent). A numbered item
    takes the first marker of its new level: "2. b" indented under "1. a" becomes
    "\\ta) b", and back. Renumbering then fits it into a list it joins."""
    line = lines[n]
    moved = change(line)
    info = classify(lines[: n + 1])[n]
    if info.kind != "ordered" or moved == line:
        return moved
    new_indent = moved[: len(moved) - len(moved.lstrip(" \t"))]
    text = line[info.content :]
    space = line[len(line[: info.content].rstrip()) : info.content]
    level = ordered_levels(classify([*lines[:n], f"{new_indent}1. {text}"]))[n]
    style = LEVEL_STYLES[level % 3]
    delim = info.delim if style == info.style else DEFAULT_DELIMS[style]
    return f"{new_indent}{format_number(1, style)}{delim}{space}{text}"


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


def retype_shorthand(line: str) -> tuple[int, str] | None:
    """ "2. - " -> (length, "- "); "- 1. " -> (length, "1. "); "\\t- a) " -> (length,
    "\\ta) "): an empty item whose marker was just typed again as another kind of list
    (Notion does the same). The new marker is kept as typed."""
    m = _RETYPE.match(line)
    if m is None or (m.group(3) == m.group(2) and m.group(2) in "-*+"):
        return None  # "- - " is not a new marker but the start of a rule ("- - -")
    return m.end(), f"{m.group(1)}{m.group(3)} "


def shorthand(lines: list[str], infos: list[LineInfo], n: int) -> tuple[int, str] | None:
    """The shorthand just typed at the start of line n, as (length replaced, new start),
    or None. Never inside code, and only if the result is a list item where it stands:
    "\\t- a) " under "1." becomes "\\ta) ", but "- a) first option" stays as typed."""
    if infos[n].kind in ("code", "fence"):
        return None
    line = lines[n]
    found = None
    if infos[n].kind in LIST_KINDS:
        found = retype_shorthand(line)
    found = found or task_shorthand(line)
    if found is None:
        return None
    length, new = found
    candidate = classify([*lines[:n], new + line[length:]])[n]
    return found if candidate.kind in LIST_KINDS else None


def ordered_levels(infos: list[LineInfo]) -> list[int]:
    """How many numbered lists each line is nested in (0 for a top-level "1."): the
    level picks the marker a new item gets. Bullets in between don't count, a paragraph
    ends the lists, blank lines don't."""
    levels = []
    # (depth, numbered lists up to and including it) of the list items above
    stack: list[tuple[int, int]] = []
    for info in infos:
        if info.kind in LIST_KINDS:
            while stack and stack[-1][0] >= info.depth:
                stack.pop()
            level = stack[-1][1] if stack else 0
            levels.append(level)
            stack.append((info.depth, level + (info.kind == "ordered")))
        else:
            if info.kind != "blank":
                stack.clear()
            levels.append(0)
    return levels


MAX_ROMAN = 3999  # larger numbers (a pasted phone number, say) are written as digits
LEVEL_STYLES = ("1", "a", "i")  # a new item's style by level: 1. / a) / i., then again
DEFAULT_DELIMS = {"1": ".", "a": ")", "i": "."}
_ROMAN_DIGITS = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _letters(n: int) -> str:
    """1 -> a, 26 -> z, 27 -> aa (n >= 1)"""
    out = ""
    while n > 0:
        n, rest = divmod(n - 1, 26)
        out = chr(ord("a") + rest) + out
    return out


def _roman(n: int) -> str:
    """1 -> i, 14 -> xiv (1 <= n <= MAX_ROMAN)"""
    out = ""
    for value, digits in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"),
                          (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"),
                          (4, "iv"), (1, "i")):  # fmt: skip
        count, n = divmod(n, value)
        out += digits * count
    return out


def _letters_value(token: str) -> int:
    """a -> 1, z -> 26, aa -> 27"""
    n = 0
    for ch in token:
        n = n * 26 + ord(ch) - ord("a") + 1
    return n


def _roman_value(token: str) -> int | None:
    """iv -> 4; None if it isn't a roman numeral written the usual way ("iiii", "mix"
    is fine: 1009)."""
    if any(ch not in _ROMAN_DIGITS for ch in token):
        return None
    total = 0
    for i, ch in enumerate(token):
        value = _ROMAN_DIGITS[ch]
        following = _ROMAN_DIGITS[token[i + 1]] if i + 1 < len(token) else 0
        total += -value if value < following else value
    return total if 1 <= total <= MAX_ROMAN and _roman(total) == token else None


def format_number(number: int, style: str) -> str:
    """A list number in a style: (3, "a") -> "c", (4, "i") -> "iv". Numbers letters or
    roman numerals can't write (0, or past MAX_ROMAN) are written as digits."""
    if style == "a" and number >= 1:
        return _letters(number)
    if style == "i" and 1 <= number <= MAX_ROMAN:
        return _roman(number)
    return str(number)


def list_label(number: int, level: int, delim: str = ".") -> str:
    """A new item's marker at this level: 1. at the top, a) one list in, i. two in,
    then again (like Notion); `delim` is for digits."""
    style = LEVEL_STYLES[level % 3]
    return f"{format_number(number, style)}{delim if style == '1' else DEFAULT_DELIMS[style]}"


def marker_text(line: str, info: LineInfo) -> str:
    """A numbered item's marker as written ("b)"), which is what is drawn for it."""
    return line[info.hidden : info.content].strip()


def step_out(lines: list[str], infos: list[LineInfo], n: int) -> str:
    """Enter on the empty nested item at line n: its line one level out, as an item of
    the list it steps back into ("\\t- " under "1. first" -> "2. ")."""
    info = infos[n]
    target = info.depth - 1
    for above in range(n - 1, -1, -1):
        other = infos[above]
        if other.kind == "blank" or (other.kind in LIST_KINDS and other.depth > target):
            continue
        if other.kind in LIST_KINDS and other.depth == target:
            return continuation(lines[above], other)  # as if Enter was pressed there
        break
    return outdent(lines[n])


def renumber(lines: list[str], infos: list[LineInfo]) -> list[tuple[int, int, int, str]]:
    """Edits that number each ordered list 1, 2, 3... from its first item's number.

    A list runs over items of the same depth; deeper items and blank lines in between
    don't break it. Each item keeps its style (1 / a / i). Returns (line, start column,
    end column, new number as written)."""
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
                end = len(lines[i][: info.content].rstrip()) - len(info.delim)
                edits.append((i, start, end, format_number(expected, info.style)))
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


def plain_text(line: str, start: int = 0) -> str:
    """A line from column `start` as it reads, without inline markers ("**a** [b](u)" ->
    "a b")."""
    return styled_text(line, start)[0]


def styled_text(line: str, start: int = 0) -> tuple[str, list[Span]]:
    """A line from column `start` as it reads, and its inline styles with columns in
    that text (spans may overlap: "**a *b** c*")."""
    spans = inline_spans(line, start)
    hidden: set[int] = set()
    for sp in spans:
        hidden.update(range(sp.start, sp.inner_start))
        hidden.update(range(sp.inner_end, sp.end))
    at = [0] * (len(line) + 1)  # column in the line -> column in the text
    out = []
    for i, ch in enumerate(line):
        at[i + 1] = at[i]
        if i >= start and i not in hidden:
            out.append(ch)
            at[i + 1] += 1
    styles = [
        Span(sp.kind, at[sp.inner_start], at[sp.inner_end], at[sp.inner_start],
             at[sp.inner_end], sp.url)
        for sp in spans
        if at[sp.inner_end] > at[sp.inner_start]
    ]  # fmt: skip
    return "".join(out), styles


# --- outline ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Heading:
    line: int
    level: int
    text: str


def code_blocks(infos: list[LineInfo]) -> list[tuple[int, int]]:
    """(first, last) line of each fenced code block, fences included; an unclosed block
    runs to the end of the note."""
    blocks = []
    n = 0
    while n < len(infos):
        if infos[n].kind != "fence":
            n += 1
            continue
        end = n + 1
        while end < len(infos) and infos[end].kind == "code":
            end += 1
        blocks.append((n, min(end, len(infos) - 1)))  # the closing fence, or the last line
        n = end + 1
    return blocks


def outline(lines: list[str], infos: list[LineInfo] | None = None) -> list[Heading]:
    """The note's headings, in order (not ones inside code blocks, nor a bare "# ")."""
    infos = infos if infos is not None else classify(lines)
    headings = []
    for n, (line, info) in enumerate(zip(lines, infos, strict=True)):
        if info.kind == "heading" and (text := plain_text(line, info.content).strip()):
            headings.append(Heading(n, info.level, text))
    return headings


def section_at(headings: list[Heading], line: int) -> int | None:
    """Index of the heading whose section holds `line` (the last one at or above it)."""
    found = None
    for i, heading in enumerate(headings):
        if heading.line > line:
            break
        found = i
    return found
