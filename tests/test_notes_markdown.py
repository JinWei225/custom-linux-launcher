import pytest

from launcher.notes_markdown import (
    LineInfo,
    classify,
    continuation,
    indent,
    is_empty_item,
    outdent,
    renumber,
    task_shorthand,
    toggle_task,
    without_marker,
)


def one(line: str) -> LineInfo:
    return classify([line])[0]


@pytest.mark.parametrize(
    "line, kind",
    [
        ("", "blank"),
        ("   ", "blank"),
        ("plain text", "text"),
        ("# Title", "heading"),
        ("### Three", "heading"),
        ("####### seven", "text"),
        ("#hashtag", "text"),
        ("#", "text"),  # a heading needs the space: typing "#" alone does nothing yet
        ("# ", "heading"),
        ("- item", "bullet"),
        ("* item", "bullet"),
        ("+ item", "bullet"),
        ("- ", "bullet"),
        ("-5 degrees", "text"),
        ("-", "text"),
        ("\t- nested", "bullet"),
        ("- [ ] todo", "task"),
        ("- [x] done", "task"),
        ("* [X] done", "task"),
        ("- [ ]", "task"),
        ("- [ ]x", "bullet"),
        ("1. first", "ordered"),
        ("12) twelve", "ordered"),
        ("1.5 kg", "text"),
        ("2025. was a year", "ordered"),
        ("> quoted", "quote"),
        (">> deeper", "quote"),
        (">", "text"),  # waits for the space
        ("> ", "quote"),
        (">>", "text"),
        (">> ", "quote"),
        ("->x", "text"),
        (">x", "text"),
        ("---", "rule"),
        ("***", "rule"),
        ("_ _ _", "rule"),
        ("- - -", "rule"),
        ("--", "text"),
        ("```", "fence"),
        ("```python", "fence"),
        ("~~~", "fence"),
        ("``` a`b", "text"),
    ],
)
def test_kinds(line, kind):
    assert one(line).kind == kind


def test_marker_positions():
    h = one("## Week 3")
    assert (h.level, h.marker, h.content, h.hidden) == (2, 3, 3, 0)
    b = one("\t- item")
    assert (b.hidden, b.content, b.delim, b.indent) == (3, 3, "-", "\t")
    t = one("- [x] done")
    assert (t.hidden, t.content, t.checked) == (6, 6, True)
    o = one("\t3. third")
    assert (o.hidden, o.marker, o.content, o.number, o.delim) == (1, 4, 4, 3, ".")
    q = one(">> deep")
    assert (q.depth, q.hidden, q.content) == (2, 3, 3)
    r = one("---")
    assert r.marker == 3


def test_code_blocks():
    lines = ["text", "```python", "# not a heading", "- not a list", "```", "- a list"]
    kinds = [i.kind for i in classify(lines)]
    assert kinds == ["text", "fence", "code", "code", "fence", "bullet"]


def test_code_fence_rules():
    # A closing fence needs the same character, at least as long, nothing after it.
    lines = ["````", "```", "~~~", "```` x", "````", "after"]
    assert [i.kind for i in classify(lines)] == ["fence", "code", "code", "code", "fence", "text"]
    # An unclosed fence runs to the end.
    assert [i.kind for i in classify(["```", "a", "b"])] == ["fence", "code", "code"]


def test_list_depth_follows_indentation_style():
    tabs = ["- a", "\t- b", "\t\t- c", "\t- d", "- e"]
    assert [i.depth for i in classify(tabs)] == [0, 1, 2, 1, 0]
    two = ["- a", "  - b", "    - c", "  - d"]
    assert [i.depth for i in classify(two)] == [0, 1, 2, 1]
    four = ["1. a", "    1. b", "        - c"]
    assert [i.depth for i in classify(four)] == [0, 1, 2]
    # A paragraph ends the list; blank lines don't.
    assert [i.depth for i in classify(["- a", "", "\t- b", "text", "\t- c"])] == [0, 0, 1, 0, 0]


def test_continuation():
    assert continuation("- item", one("- item")) == "- "
    assert continuation("\t* item", one("\t* item")) == "\t* "
    assert continuation("- [x] done", one("- [x] done")) == "- [ ] "
    assert continuation("\t9) nine", one("\t9) nine")) == "\t10) "
    assert continuation(">> q", one(">> q")) == "> > "
    assert continuation("text", one("text")) is None
    assert continuation("# h", one("# h")) is None


def test_empty_items():
    for line in ("- ", "- [ ] ", "1. ", "> ", "\t-  "):
        assert is_empty_item(line, one(line)), line
    for line in ("- x", "text", "", "# "):
        assert not is_empty_item(line, one(line)), line


def test_indent_and_outdent():
    assert indent("- a") == "\t- a"
    assert outdent("\t\t- a") == "\t- a"
    assert outdent("      - a") == "  - a"
    assert outdent("  - a") == "- a"
    assert outdent("- a") == "- a"


def test_without_marker():
    for line, plain in (
        ("- item", "item"),
        ("\t- [x] done", "done"),
        ("3. third", "third"),
        ("> quote", "quote"),
        ("## Title", "Title"),
        ("text", "text"),
    ):
        assert without_marker(line, one(line)) == plain


def test_toggle_task():
    assert toggle_task("- [ ] a", one("- [ ] a")) == "- [x] a"
    assert toggle_task("\t- [X] a", one("\t- [X] a")) == "\t- [ ] a"
    assert toggle_task("- [ ]", one("- [ ]")) == "- [x]"
    assert toggle_task("- a", one("- a")) == "- a"


def test_task_shorthand():
    assert task_shorthand("[] ") == (3, "- [ ] ")
    assert task_shorthand("[ ] buy milk") == (4, "- [ ] ")
    assert task_shorthand("- [] ") == (5, "- [ ] ")
    assert task_shorthand("\t[] x") == (4, "\t- [ ] ")
    assert task_shorthand("- [ ] already") is None
    assert task_shorthand("[]") is None  # not until the space
    assert task_shorthand("text [] ") is None


def test_renumber():
    lines = ["1. a", "1. b", "\t1. x", "\t5. y", "1. c", "", "7. d", "text", "3. new"]
    edits = renumber(lines, classify(lines))
    assert edits == [(1, 0, 1, "2"), (3, 1, 2, "2"), (4, 0, 1, "3"), (6, 0, 1, "4")]


def test_renumber_keeps_start_and_restarts_after_bullets():
    lines = ["3. a", "9. b", "- bullet", "8. new list"]
    assert renumber(lines, classify(lines)) == [(1, 0, 1, "4")]
    lines = ["9. a", "10. b", "11. c"]
    assert renumber(lines, classify(lines)) == []
    lines = ["9. a", "9. b", "9. c"]  # 9 -> 10 changes the width
    assert renumber(lines, classify(lines)) == [(1, 0, 1, "10"), (2, 0, 1, "11")]


def test_bare_quote_marker_continues_a_quote_only():
    kinds = [i.kind for i in classify(["> first", ">", "> second", "", ">"])]
    assert kinds == ["quote", "quote", "quote", "blank", "text"]
