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
