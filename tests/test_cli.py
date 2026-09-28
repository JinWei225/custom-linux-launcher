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


def test_run_action():
    assert requested_action(parse_args(["--run", "app:code.desktop"])) == (
        "run",
        "app:code.desktop",
    )


def test_settings_flags():
    args = parse_args(["--settings", "--edit", "quicklink:GitHub"])
    assert args.settings and args.edit == "quicklink:GitHub"


def test_notes_commands_talk_to_a_running_notes(monkeypatch):
    import launcher.notes.app as notes_app
    from launcher import NOTES_APP_ID
    from launcher.__main__ import main
    from launcher.client import SendStatus

    sent, started = [], []
    monkeypatch.setattr(
        "launcher.client.send", lambda *a, **k: (sent.append((a, k)), SendStatus.SENT)[1]
    )
    monkeypatch.setattr(notes_app, "run_notes", lambda *a, **k: started.append((a, k)))
    for argv, request in [
        (["--notes"], ("toggle", None)),
        (["--notes", "--open", "FYP/Week 3.md"], ("open-note", "FYP/Week 3.md")),
        (["--notes", "--new", "Lecture 4"], ("new-note", "Lecture 4")),
        (["--notes", "--background"], ("preload", None)),  # never shows a running Notes
    ]:
        sent.clear()
        assert main(argv) == 0
        assert sent == [(request, {"app_id": NOTES_APP_ID})]
    assert started == []

    monkeypatch.setattr("launcher.client.send", lambda *a, **k: SendStatus.NOT_RUNNING)
    main(["--notes", "--background"])
    assert started == [((None, None), {"background": True})]  # not running: start it hidden


def test_notes_object_path():
    cmd = build_command("toggle", None, {}, app_id=APP_ID + ".Notes")
    assert cmd[cmd.index("--dest") + 1] == "io.github.jinwei.Launcher.Notes"
    assert cmd[cmd.index("--object-path") + 1] == "/io/github/jinwei/Launcher/Notes"
    assert build_command("toggle", None, {})[6] == OBJECT_PATH
