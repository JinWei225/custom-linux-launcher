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
  quote    "> text"        hidden "> " (a bar is drawn instead)
  rule     "---"           the whole line (a line is drawn instead)
  fence    "```python"     shown dim; lines between two fences are "code"
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
        if prefix.endswith((" ", "\t")) or line.rstrip() == prefix.rstrip():
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
