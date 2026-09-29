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
    assert (o.hidden, o.marker, o.content, o.number, o.delim) == (4, 4, 4, 3, ".")
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


# --- inline styles ---------------------------------------------------------------------

from launcher.notes_markdown import (  # noqa: E402
    attachment_links,
    attachment_name,
    inline_spans,
    toggle_wrap,
)


def styles(text: str, start: int = 0) -> list[tuple[str, str]]:
    return [(s.kind, text[s.inner_start : s.inner_end]) for s in inline_spans(text, start)]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("**bold**", [("bold", "bold")]),
        ("__bold__", [("bold", "bold")]),
        ("*italic*", [("italic", "italic")]),
        ("_italic_", [("italic", "italic")]),
        ("~~gone~~", [("strike", "gone")]),
        ("`x = 1`", [("code", "x = 1")]),
        ("``a ` b``", [("code", "a ` b")]),
        ("==key==", [("highlight", "key")]),
        ("<u>under</u>", [("underline", "under")]),
        ("***both***", [("bold", "both"), ("italic", "both")]),
        ("**bold *and italic***", [("bold", "bold *and italic*"), ("italic", "and italic")]),
        ("a **b** c *d* e", [("bold", "b"), ("italic", "d")]),
        # not styles
        ("**not closed", []),
        ("** spaced **", []),
        ("snake_case_name", []),
        ("2 * 3 * 4", []),
        ("\\*escaped\\*", []),
        ("~single~", []),
        ("a == b", []),
        ("****", []),
        # nothing inside code is styled
        ("`**not bold**`", [("code", "**not bold**")]),
    ],
)
def test_inline_styles(text, expected):
    assert styles(text) == expected


def test_links_and_urls():
    text = "see [the docs](https://gnome.org/a) or https://example.com/x. (www.b.org)"
    spans = inline_spans(text)
    assert [(s.kind, text[s.inner_start : s.inner_end], s.url) for s in spans] == [
        ("link", "the docs", "https://gnome.org/a"),
        ("url", "https://example.com/x", "https://example.com/x"),
        ("url", "www.b.org", "https://www.b.org"),
    ]
    link = spans[0]
    assert text[link.start : link.inner_start] == "[" and text[link.inner_end : link.end] == (
        "](https://gnome.org/a)"
    )
    assert styles("[**bold** link](u)") == [("link", "**bold** link"), ("bold", "bold")]
    assert styles("<https://a.b/c>") == [("url", "https://a.b/c")]
    assert styles("[x](<a b.md>)")[0] == ("link", "x")
    assert inline_spans("[x](<a b.md>)")[0].url == "a b.md"
    assert styles("![alt](pic.png) text") == [("image", "alt")]


def test_inline_start_column_skips_markers():
    # In "* item *x*" the list bullet "* " must not pair with a later "*".
    assert styles("* item *x*", start=2) == [("italic", "x")]


def test_image_lines():
    info = classify(["![](attachments/a.png)"])[0]
    assert (info.kind, info.url) == ("image", "attachments/a.png")
    assert classify(["  ![shot](<attachments/a b.png>)  "])[0].url == "attachments/a b.png"
    assert classify(["text ![](a.png)"])[0].kind == "text"
    assert classify(["![](a b.png)"])[0].kind == "text"  # a space needs <...>


@pytest.mark.parametrize(
    "text, a, z, kind, expected",
    [
        ("make this bold", 5, 9, "bold", ("make **this** bold", 7, 11)),
        ("make **this** bold", 7, 11, "bold", ("make this bold", 5, 9)),  # unwrap
        ("make **this** bold", 5, 13, "bold", ("make this bold", 5, 9)),  # markers selected
        ("word", 0, 4, "italic", ("*word*", 1, 5)),
        ("**word**", 2, 6, "italic", ("***word***", 3, 7)),  # bold, not italic: wrap
        ("x", 1, 1, "code", ("x``", 2, 2)),  # nothing selected: empty markers
        ("u", 0, 1, "underline", ("<u>u</u>", 3, 4)),
    ],
)
def test_toggle_wrap(text, a, z, kind, expected):
    assert toggle_wrap(text, a, z, kind) == expected


def test_attachment_names_and_links():
    assert attachment_name("Lecture 3", "20260928-1213", set()) == "Lecture-3-20260928-1213.png"
    taken = {"Lecture-3-20260928-1213.png"}
    assert attachment_name("Lecture 3", "20260928-1213", taken) == "Lecture-3-20260928-1213-2.png"
    assert attachment_name("中文 笔记", "1", set()) == "中文-笔记-1.png"
    assert attachment_name("???", "1", set()) == "image-1.png"
    text = "![](attachments/a.png)\n[doc](attachments/b.pdf) ![](other/c.png) ![](https://x/y.png)"
    assert attachment_links(text) == ["attachments/a.png", "attachments/b.pdf"]


# --- outline ---------------------------------------------------------------------------

from launcher.notes_markdown import Heading, outline, plain_text, section_at  # noqa: E402


@pytest.mark.parametrize(
    "line, start, expected",
    [
        ("plain", 0, "plain"),
        ("**bold** and *it*", 0, "bold and it"),
        ("see [the docs](https://a.b) now", 0, "see the docs now"),
        ("<https://a.b> and https://c.d", 0, "https://a.b and https://c.d"),
        ("## Week **3**", 3, "Week 3"),
        ("`**code**` ~~x~~ ==y== <u>z</u>", 0, "**code** x y z"),
        ("2 * 3 * 4", 0, "2 * 3 * 4"),
    ],
)
def test_plain_text(line, start, expected):
    assert plain_text(line, start) == expected


def test_outline():
    lines = [
        "# Lecture 3",
        "intro",
        "## Part **one**",
        "```",
        "# not a heading",
        "```",
        "#",
        "## ",
        "### [Linked](x.md) detail",
        "#hashtag",
    ]
    assert outline(lines) == [
        Heading(0, 1, "Lecture 3"),
        Heading(2, 2, "Part one"),
        Heading(8, 3, "Linked detail"),
    ]
    assert outline([]) == []


def test_section_at():
    headings = [Heading(2, 1, "a"), Heading(5, 2, "b"), Heading(9, 2, "c")]
    assert [section_at(headings, n) for n in (0, 1, 2, 4, 5, 8, 9, 50)] == [
        None, None, 0, 0, 1, 1, 2, 2,
    ]  # fmt: skip
    assert section_at([], 3) is None


from launcher.notes_markdown import styled_text  # noqa: E402


def test_styled_text():
    text, styles = styled_text("see **bold [link](https://a.b)** and `x`")
    assert text == "see bold link and x"
    assert [(s.kind, text[s.start : s.end], s.url) for s in styles] == [
        ("bold", "bold link", ""),
        ("link", "link", "https://a.b"),
        ("code", "x", ""),
    ]
    text, styles = styled_text("- item *it*", start=2)
    assert (text, [(s.kind, s.start, s.end) for s in styles]) == ("item it", [("italic", 5, 7)])
    assert styled_text("****") == ("****", [])


# --- numbered sub-lists ----------------------------------------------------------------

from launcher.notes_markdown import (  # noqa: E402
    list_label,
    ordered_levels,
    step_out,
    sublist_shorthand,
)


@pytest.mark.parametrize(
    "number, level, delim, label",
    [
        (1, 0, ".", "1."),
        (12, 0, ")", "12)"),
        (1, 1, ".", "a)"),
        (3, 1, ".", "c)"),
        (26, 1, ".", "z)"),
        (27, 1, ".", "aa)"),
        (1, 2, ".", "i."),
        (4, 2, ".", "iv."),
        (9, 2, ".", "ix."),
        (14, 2, ".", "xiv."),
        (2, 3, ".", "2."),  # then again
    ],
)
def test_list_label(number, level, delim, label):
    assert list_label(number, level, delim) == label


def test_ordered_levels_count_numbered_lists_only():
    lines = [
        "1. top",  # 0
        "\t1. sub",  # 1
        "\t\t1. subsub",  # 2
        "\t\t\t1. again",  # 3 -> shown as 1. again
        "\t- bullet",  # bullets don't count
        "\t\t1. under it",  # 1: inside one numbered list
        "",  # blank lines don't end a list
        "2. top",  # 0
        "text",  # a paragraph ends it
        "\t1. alone",  # 0
    ]
    levels = ordered_levels(classify(lines))
    assert [levels[i] for i in (0, 1, 2, 3, 5, 7, 9)] == [0, 1, 2, 3, 1, 0, 0]
    labels = [list_label(1, levels[i]) for i in (0, 1, 2, 3)]
    assert labels == ["1.", "a)", "i.", "1."]


@pytest.mark.parametrize(
    "line, expected",
    [
        ("\ta) ", (4, "\t1. ")),
        ("\ti. x", (4, "\t1. ")),
        ("    a. ", (7, "    1. ")),
        ("a) ", None),  # not indented: prose like "a) first point" stays text
        ("\ta)", None),  # not until the space
        ("\tb) ", None),
        ("\t1. x", None),
    ],
)
def test_sublist_shorthand(line, expected):
    assert sublist_shorthand(line) == expected


def test_step_out_joins_the_list_above():
    lines = ["1. first", "\t- bullet", "\t- "]
    assert step_out(lines, classify(lines), 2) == "2. "
    lines = ["3) first", "\t1. sub", "\t\t- deep", "\t\t- "]
    assert step_out(lines, classify(lines), 3) == "\t2. "
    lines = ["- [x] task", "", "\t1. sub", "\t2. "]
    assert step_out(lines, classify(lines), 3) == "- [ ] "
    lines = ["- a", "\t\t- "]  # one level in, however far it is indented
    assert step_out(lines, classify(lines), 1) == "- "
    lines = ["text", "\t- "]  # no list to step into: just outdent
    assert step_out(lines, classify(lines), 1) == "- "


@pytest.mark.parametrize(
    "line, expected",
    [
        ("2. - ", (5, "- ")),
        ("\t1. * ", (6, "\t* ")),
        ("- 1. ", (5, "1. ")),
        ("\t- 1) ", (6, "\t1. ")),
        ("- 3) ", None),  # only "1." starts a list: "- 2024. was a good year" stays
        ("- 2024. ", None),
        ("2. - x", None),  # only right after the marker is typed on an empty item
        ("2. -", None),
        ("- - ", None),  # the same marker again is a rule being typed: "- - -"
        ("* * ", None),
        ("- * ", (4, "* ")),
        ("\t1. a) ", (7, "\t1. ")),  # already numbered (shown as a)): stays numbered
        ("\t- i. ", (6, "\t1. ")),
    ],
)
def test_retype_shorthand(line, expected):
    from launcher.notes_markdown import retype_shorthand

    assert retype_shorthand(line) == expected


def _shorthand(lines):
    from launcher.notes_markdown import shorthand

    return shorthand(lines, classify(lines), len(lines) - 1)


@pytest.mark.parametrize(
    "lines, expected",
    [
        (["[] "], (3, "- [ ] ")),
        (["1. top", "\ta) "], (4, "\t1. ")),  # a) one list in: shown as a)
        (["1. top", "\t1. a", "\t\ti. "], (5, "\t\t1. ")),  # i. two lists in
        (["1. top", "\t- a) "], (6, "\t1. ")),
        (["1. top", "\t- "], None),
        (["- a) first option"], None),  # prose after a bullet stays as typed
        (["- a) "], None),
        (["- i. note"], None),
        (["- item", "\ta) "], None),  # under a bullet it would show 1., not a)
        (["1. top", "\ti. "], None),  # one list in it would show a), not i.
        (["    a. "], None),  # no list around it at all
        (["```yaml", "  - - "], None),  # never inside code
        (["```", "\ta) "], None),
        (["```", "[] "], None),
        (["```", "x", "```", "\t- 1. "], (6, "\t1. ")),  # after the block: as usual
    ],
)
def test_shorthand_in_context(lines, expected):
    assert _shorthand(lines) == expected
