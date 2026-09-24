import pytest

from launcher import APP_ID, OBJECT_PATH
from launcher.__main__ import parse_args, requested_action
from launcher.client import build_command, gvariant_string


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], ("toggle", "all")),
        (["--mode", "clipboard"], ("toggle", "clipboard")),
        (["--show", "--mode", "files"], ("show", "files")),
        (["--hide"], ("hide", None)),
        (["--reload"], ("reload", None)),
        (["--quit"], ("quit", None)),
    ],
)
def test_requested_action(argv, expected):
    assert requested_action(parse_args(argv)) == expected


def test_conflicting_flags_rejected():
    with pytest.raises(SystemExit):
        parse_args(["--show", "--quit"])


def test_gvariant_string_escapes():
    assert gvariant_string("it's") == "'it\\'s'"
    assert gvariant_string("a\\b") == "'a\\\\b'"


def test_build_command_without_token():
    cmd = build_command("toggle", "files", {})
    assert cmd[:9] == [
        "gdbus", "call", "--session", "--dest", APP_ID,
        "--object-path", OBJECT_PATH, "--method", "org.gtk.Actions.Activate",
    ]  # fmt: skip
    assert cmd[9:] == ["'toggle'", "[<'files'>]", "@a{sv} {}"]


def test_build_command_forwards_activation_token():
    cmd = build_command("quit", None, {"XDG_ACTIVATION_TOKEN": "tok_1"})
    assert cmd[-2:] == ["@av []", "{'activation-token': <'tok_1'>}"]
