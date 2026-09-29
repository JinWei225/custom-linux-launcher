import pytest

from launcher.window import edit_target


@pytest.mark.parametrize(
    ("result_id", "target"),
    [
        ("app:code.desktop", "app:code.desktop"),
        ("quicklink:GitHub", "quicklink:GitHub"),
        ("websearch:Google Search", "quicklink:Google Search"),
        ("file:/home/u/a.txt", None),
        ("command:quit", None),
    ],
)
def test_ctrl_e_targets(result_id, target):
    assert edit_target(result_id) == target


def _results(*ids):
    from launcher.providers.base import Result

    return [Result(id=i, title=i) for i in ids]


@pytest.mark.parametrize(
    ("selected", "index", "ids", "expected"),
    [
        ("clip:5", 2, ("clip:9", "clip:7", "clip:6", "clip:5"), 3),  # a new copy above it
        ("clip:5", 0, ("clip:5", "clip:4"), 0),
        ("clip:5", 1, ("clip:9", "clip:4", "clip:3"), 1),  # gone: same position
        ("clip:5", 7, ("clip:9", "clip:4"), 1),  # gone, past the end: the last row
        (None, 0, ("a",), 0),
        ("x", 3, (), 0),
    ],
)
def test_keep_selection_follows_the_entry(selected, index, ids, expected):
    from launcher.window import keep_selection

    assert keep_selection(selected, index, _results(*ids)) == expected
