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
    format_number,
    list_label,
    marker_text,
    ordered_levels,
    reindent,
    step_out,
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
        (3999, 2, ".", "mmmcmxcix."),
        (4000, 2, ".", "4000."),  # past roman numerals: digits, not thousands of m's
        (555123456, 2, ".", "555123456."),
        (0, 1, ".", "0)"),
        (702, 1, ".", "zz)"),
    ],
)
def test_list_label_is_a_new_items_marker_at_its_level(number, level, delim, label):
    assert list_label(number, level, delim) == label


def test_letters_and_roman_numerals_read_back():
    from launcher.notes_markdown import _letters_value, _roman_value

    for n in range(1, 4000):
        assert _roman_value(format_number(n, "i")) == n
        assert _letters_value(format_number(n, "a")) == n
    for token in ("iiii", "vx", "mmmm", "dim", "ic", "abc"):
        assert _roman_value(token) is None


def _items(lines):
    return [
        (i.kind, i.number, i.style, i.depth) if i.kind == "ordered" else i.kind
        for i in classify(lines)
    ]


@pytest.mark.parametrize(
    "lines, expected",
    [
        # As the file says: 1. at the top, a) one numbered list in, i. two in, then 1.
        (["1. top", "\ta) sub", "\t\ti. deeper", "\t\t\t1. again"],
         [("ordered", 1, "1", 0), ("ordered", 1, "a", 1), ("ordered", 1, "i", 2),
          ("ordered", 1, "1", 3)]),
        (["1. top", "\tc. third", "\ti) ninth"],
         [("ordered", 1, "1", 0), ("ordered", 3, "a", 1), ("ordered", 9, "a", 1)]),
        (["1. top", "\ta) x", "\t\tiv. four", "\t\tvs. prose"],
         [("ordered", 1, "1", 0), ("ordered", 1, "a", 1), ("ordered", 4, "i", 2), "text"]),
        # Where letters don't belong they are text, as prose.
        (["a) first point"], ["text"]),
        (["- item", "\ta) under a bullet"], ["bullet", "text"]),
        (["    a. alone"], ["text"]),
        (["1. top", "\ta)"], [("ordered", 1, "1", 0), "text"]),  # not until the space
        (["1. top", "\tvs. that"], [("ordered", 1, "1", 0), "text"]),  # two letters: no
        (["1. top", "\ti. roman one in"], [("ordered", 1, "1", 0), ("ordered", 9, "a", 1)]),
        # Longer markers continue their list: z) aa), xxxix. xl.
        (["1. x", "\tz) y", "\taa) z", "\taa) inserted"],
         [("ordered", 1, "1", 0), ("ordered", 26, "a", 1), ("ordered", 27, "a", 1),
          ("ordered", 27, "a", 1)]),
        (["1. x", "\ta) y", "\t\txxxix. z", "\t\txl. w"],
         [("ordered", 1, "1", 0), ("ordered", 1, "a", 1), ("ordered", 39, "i", 2),
          ("ordered", 40, "i", 2)]),
        (["1. x", "\ta) y", "\t\txl. alone"], [("ordered", 1, "1", 0), ("ordered", 1, "a", 1),
                                              "text"]),
        # Digits count everywhere: notes written with "\t1." sub-items keep them.
        (["1. top", "\t1. sub"], [("ordered", 1, "1", 0), ("ordered", 1, "1", 1)]),
        # A bullet in between doesn't count as a numbered list.
        (["1. top", "\t- bullet", "\t\ta) lettered"],
         [("ordered", 1, "1", 0), "bullet", ("ordered", 1, "a", 2)]),
    ],
)  # fmt: skip
def test_lettered_and_roman_items_where_they_belong(lines, expected):
    assert _items(lines) == expected


def test_marker_text_is_what_is_drawn():
    for line, text in (("\tb) x", "b)"), ("\t\tiv. y", "iv."), ("12) z", "12)")):
        lines = ["1. top", "\ta) x", line] if line.startswith("\t\t") else ["1. top", line]
        assert marker_text(line, classify(lines)[-1]) == text


def test_ordered_levels_count_numbered_lists_only():
    lines = [
        "1. top",  # 0
        "\ta) sub",  # 1
        "\t\ti. subsub",  # 2
        "\t\t\t1. again",  # 3
        "\t- bullet",  # bullets don't count
        "\t\ta) under it",  # 1: inside one numbered list
        "",  # blank lines don't end a list
        "2. top",  # 0
        "text",  # a paragraph ends it
        "\t1. alone",  # 0
    ]
    levels = ordered_levels(classify(lines))
    assert [levels[i] for i in (0, 1, 2, 3, 5, 7, 9)] == [0, 1, 2, 3, 1, 0, 0]


def test_enter_continues_an_items_own_style():
    from launcher.notes_markdown import continuation

    for lines, expected in (
        (["1. top", "\tb) x"], "\tc) "),
        (["1. top", "\ta) x", "\t\tiii. y"], "\t\tiv. "),
        (["1. top", "\t1. old style"], "\t2. "),
        (["1. top", "\tz) x"], "\taa) "),
    ):
        infos = classify(lines)
        assert continuation(lines[-1], infos[-1]) == expected


@pytest.mark.parametrize(
    "lines, n, deeper, expected",
    [
        (["1. a", "2. b"], 1, True, "\ta) b"),
        (["1. a", "2) b"], 1, True, "\ta) b"),
        (["1. a", "\ta) s", "\tb) t"], 2, True, "\t\ti. t"),
        (["1. a", "\ta) s", "\tb) t"], 2, False, "1. t"),  # renumbering makes it 2.
        (["1. a", "\t3) b"], 1, False, "1) b"),  # still digits: keeps its ")"
        (["1. a", "\ta) s"], 1, True, "\t\ta) s"),  # still one list in: stays a)
        (["1. a", "\ta)  two spaces"], 1, False, "1.  two spaces"),
        (["- a", "\t- b"], 1, False, "- b"),
        (["- a", "- b"], 1, True, "\t- b"),
    ],
)
def test_reindent_gives_a_numbered_item_its_new_levels_marker(lines, n, deeper, expected):
    from launcher.notes_markdown import indent, outdent

    assert reindent(lines, n, indent if deeper else outdent) == expected


def test_renumber_keeps_each_items_style():
    lines = ["1. x", "\ta) one", "\ta) two", "\t\ti. deep", "\t\ti. deeper", "\tx) three",
             "2. y"]  # fmt: skip
    edits = renumber(lines, classify(lines))
    assert edits == [(2, 1, 2, "b"), (4, 2, 3, "ii"), (5, 1, 2, "c")]
    letters = ["1. x"] + [f"\t{chr(ord('a') + i)}) item" for i in range(26)] + ["\ta) more"]
    assert renumber(letters, classify(letters)) == [(27, 1, 2, "aa")]


def test_step_out_joins_the_list_above():
    lines = ["1. first", "\t- bullet", "\t- "]
    assert step_out(lines, classify(lines), 2) == "2. "
    lines = ["3) first", "\t1. sub", "\t\t- deep", "\t\t- "]
    assert step_out(lines, classify(lines), 3) == "\t2. "
    lines = ["1. first", "\ta) sub", "\t\t- deep", "\t\t- "]
    assert step_out(lines, classify(lines), 3) == "\tb) "
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
        ("\t- 1) ", (6, "\t1) ")),  # kept as typed
        ("- 3) ", None),  # only "1." starts a list: "- 2024. was a good year" stays
        ("- 2024. ", None),
        ("2. - x", None),  # only right after the marker is typed on an empty item
        ("2. -", None),
        ("- - ", None),  # the same marker again is a rule being typed: "- - -"
        ("* * ", None),
        ("- * ", (4, "* ")),
        ("\t1. a) ", (7, "\ta) ")),
        ("\ta) a) ", (7, "\ta) ")),  # typed again: once is enough
        ("\t- i. ", (6, "\ti. ")),
        ("\tb) - ", (6, "\t- ")),
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
        (["1. top", "\t- a) "], (6, "\ta) ")),  # lettered, as typed
        (["1. top", "\ta) x", "\t\t- i. "], (7, "\t\ti. ")),
        (["1. top", "\ta) "], None),  # already an item: nothing to rewrite
        (["1. top", "\tb) - "], (6, "\t- ")),
        (["1. top", "\t- "], None),
        (["- a) first option"], None),  # prose after a bullet stays as typed
        (["- a) "], None),
        (["- i. note"], None),
        (["- item", "\t- a) "], None),  # under a bullet a) isn't an item
        (["no. - "], None),  # not a list item to begin with
        (["```yaml", "  - - "], None),  # never inside code
        (["```", "\t- a) "], None),
        (["```", "[] "], None),
        (["```", "x", "```", "\t- 1. "], (6, "\t1. ")),  # after the block: as usual
    ],
)
def test_shorthand_in_context(lines, expected):
    assert _shorthand(lines) == expected


def test_code_block_ranges():
    from launcher.notes_markdown import code_blocks

    lines = ["text", "```py", "x", "y", "```", "", "~~~", "z"]
    assert code_blocks(classify(lines)) == [(1, 4), (6, 7)]  # the last runs to the end
    assert code_blocks(classify(["```", "```"])) == [(0, 1)]
    assert code_blocks(classify(["```"])) == [(0, 0)]
    assert code_blocks(classify(["no code"])) == []


def test_classify_is_the_same_whatever_was_classified_before():
    # Per-line results are cached and shared: a line's depth must still come from
    # where it is, not from where the same text was seen first.
    first = classify(["- a", "\t- b"])
    second = classify(["\t- b"])
    assert first[1].depth == 1 and second[0].depth == 0
    assert classify(["- a", "\t- b"]) == first
