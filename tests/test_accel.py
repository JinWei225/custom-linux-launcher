import pytest

from launcher import accel


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("<Super><Shift>f", "<Shift><Super>f"),
        ("<Shift><Super>F", "<Shift><Super>f"),
        ("<Primary>space", "<Control>Space"),
        ("<ctrl><alt>t", "<Control><Alt>t"),
        ("Print", "Print"),
        ("<Super>f5", "<Super>F5"),
        ("<Super>Above_Tab", "<Super>Above_Tab"),
    ],
)
def test_normalize(raw, canonical):
    assert accel.normalize(raw) == canonical


@pytest.mark.parametrize("bad", ["", "<Super>", "<Bogus>f", "<Super>f g", "/org/path/", "['x']"])
def test_not_accelerators(bad):
    assert accel.normalize(bad) is None


def test_same_ignores_spelling():
    assert accel.same("<Control>space", "<Primary>Space")
    assert accel.same("<Super>Return", "<super>return")
    assert not accel.same("<Super>a", "<Super>b")
    assert not accel.same("<Super>a", "junk!")


def test_hotkey_problem():
    assert accel.hotkey_problem("<Super><Shift>f") is None
    assert accel.hotkey_problem("F9") is None
    assert "needs Super" in accel.hotkey_problem("<Shift>a")
    assert "not a shortcut" in accel.hotkey_problem("Super+F")
