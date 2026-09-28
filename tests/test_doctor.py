from dataclasses import replace

import pytest

from launcher import doctor
from launcher.config import Config, ConvertersConfig, NotesConfig, Snippet
from launcher.doctor import ERROR, OK, WARNING, Facts, evaluate, report


def healthy(**changes) -> Facts:
    facts = Facts(
        launcher="/home/me/.local/bin/launcher",
        service="active",
        service_enabled=True,
        daemon_running=True,
        shell_version="50.1",
        extension_on_disk=2,
        extension_shells=("50",),
        extension_state=doctor.EXT_ACTIVE,
        extension_enabled=True,
        extension_loaded=2,
        espanso=True,
        espanso_service="active",
        espanso_file=True,
        rates_age=3600,
    )
    return replace(facts, **changes)


def by_key(facts: Facts) -> dict[str, doctor.Check]:
    return {c.key: c for c in evaluate(facts)}


def test_a_healthy_setup_is_all_ok():
    checks = evaluate(healthy())
    assert [c.status for c in checks] == [OK] * len(checks)
    assert [c.key for c in checks] == [
        "config", "service", "extension", "shortcuts", "espanso", "notes", "pdf", "rates",
        "folders",
    ]  # fmt: skip
    assert report(checks).endswith("Everything is set up.")


@pytest.mark.parametrize(
    "changes, status, detail, command",
    [
        ({"service": "failed", "daemon_running": False}, ERROR, "crashed and stopped",
         ("systemctl", "--user", "enable", "--now", "launcher.service")),
        ({"service": "inactive", "daemon_running": False}, ERROR, "Not running (inactive)",
         ("systemctl", "--user", "enable", "--now", "launcher.service")),
        ({"service": "", "daemon_running": False}, ERROR, "Not installed", ()),
        ({"service": "inactive", "daemon_running": True}, WARNING, "not as the service",
         ("systemctl", "--user", "enable", "--now", "launcher.service")),
        ({"service_enabled": False}, WARNING, "won't start after you log in",
         ("systemctl", "--user", "enable", "--now", "launcher.service")),
    ],
)  # fmt: skip
def test_service(changes, status, detail, command):
    check = by_key(healthy(**changes))["service"]
    assert (check.status, check.command) == (status, command)
    assert detail in check.detail


@pytest.mark.parametrize(
    "changes, status, detail",
    [
        ({"extension_on_disk": None, "extension_state": None}, ERROR, "Not installed"),
        ({"shell_version": "51.0"}, ERROR, "Doesn't declare support for GNOME 51"),
        ({"shell_version": ""}, WARNING, "Couldn't ask GNOME Shell"),
        ({"extensions_enabled": False}, ERROR, "extensions are turned off"),
        ({"extension_state": None}, ERROR, "hasn't loaded it yet"),
        ({"extension_state": doctor.EXT_ERROR, "extension_error": "boom"}, ERROR,
         "Failed to start: boom"),
        ({"extension_state": doctor.EXT_OUT_OF_DATE}, ERROR, "out of date"),
        ({"extension_state": doctor.EXT_INACTIVE}, ERROR, "Turned off"),
        ({"extension_on_disk": 3}, WARNING, "Version 3 is installed but 2 is running"),
        ({"extension_on_disk": 1, "extension_loaded": 1}, WARNING, "older than this launcher"),
    ],
)  # fmt: skip
def test_extension(changes, status, detail):
    check = by_key(healthy(**changes))["extension"]
    assert check.status == status
    assert detail in check.detail
    assert check.fix or status == WARNING


def test_extension_fix_commands():
    turned_off = by_key(healthy(extension_state=doctor.EXT_INACTIVE))["extension"]
    assert turned_off.command == ("gnome-extensions", "enable", doctor.EXTENSION_UUID)
    all_off = by_key(healthy(extensions_enabled=False))["extension"]
    assert all_off.command[:3] == ("gsettings", "set", "org.gnome.shell")


def test_config_problems():
    broken = by_key(healthy(config_error="ui.width must be between 400 and 1600"))["config"]
    assert (broken.status, broken.detail) == (ERROR, "ui.width must be between 400 and 1600")
    odd = by_key(healthy(config_warnings=["unknown setting 'ui.colour' ignored"]))["config"]
    assert odd.status == WARNING


def test_shortcuts():
    clash = by_key(healthy(shortcut_clashes=["Launcher: Files: <Super>f is used by GNOME"]))
    assert clash["shortcuts"].status == WARNING and "<Super>f" in clash["shortcuts"].detail
    missing = by_key(healthy(shortcuts_missing=["Launcher (<Control>space)"]))["shortcuts"]
    assert missing.status == WARNING
    assert missing.command == ("/home/me/.local/bin/launcher", "--reload")


def test_espanso_only_matters_with_triggers():
    no_triggers = healthy(espanso=False, espanso_service="")
    assert by_key(no_triggers)["espanso"].status == OK
    with_trigger = Config(snippets=(Snippet(name="Sig", body="x", trigger=";sig"),))
    missing = by_key(healthy(config=with_trigger, espanso=False))["espanso"]
    assert missing.status == WARNING and "1 trigger(s) won't expand" in missing.detail
    stopped = by_key(healthy(config=with_trigger, espanso_service="failed"))["espanso"]
    assert stopped.command == ("systemctl", "--user", "restart", "espanso.service")
    no_file = by_key(healthy(config=with_trigger, espanso_file=False))["espanso"]
    assert no_file.command[-1] == "--reload"
    assert by_key(healthy(config=with_trigger))["espanso"].detail == "Running with 1 trigger(s)"


def test_notes_pdf_and_folders():
    notes = by_key(healthy(config=Config(notes=NotesConfig(folder="/root/x")),
                           notes_writable=False))["notes"]  # fmt: skip
    assert notes.status == ERROR and "/root/x" in notes.detail
    pdf = by_key(healthy(gi_cairo=False))["pdf"]
    assert pdf.status == WARNING and "sudo apt install python3-gi-cairo" in pdf.fix
    assert pdf.command == ()  # needs sudo: never run for you
    folders = by_key(healthy(missing_folders=["~/Desktop"]))["folders"]
    assert folders.status == WARNING and "~/Desktop" in folders.detail


@pytest.mark.parametrize(
    "age, currency, status, detail",
    [
        (None, True, WARNING, "Not downloaded yet"),
        (30, True, OK, "Downloaded 1 min ago"),
        (5 * 3600, True, OK, "Downloaded 5 h ago"),
        (3 * 86400, True, WARNING, "Last downloaded 3 days ago"),
        (None, False, OK, "turned off"),
    ],
)
def test_rates(age, currency, status, detail):
    config = Config(converters=ConvertersConfig(currency=currency))
    check = by_key(healthy(config=config, rates_age=age))["rates"]
    assert check.status == status and detail in check.detail


def test_rates_follow_a_long_refresh_interval():
    weekly = Config(converters=ConvertersConfig(refresh_hours=168))
    assert by_key(healthy(config=weekly, rates_age=10 * 86400))["rates"].status == OK
    assert by_key(healthy(config=weekly, rates_age=15 * 86400))["rates"].status == WARNING


def test_report_lists_fixes_and_counts():
    down = {"service": "failed", "daemon_running": False}
    text = report(evaluate(healthy(gi_cairo=False, **down)))
    assert "✗ Launcher service" in text and "! Notes PDF export" in text
    assert "→ Install it: sudo apt install python3-gi-cairo" in text
    assert text.endswith("2 problem(s), 1 serious.")
    assert "\x1b[31m✗\x1b[0m" in report(evaluate(healthy(**down)), color=True)


def test_notes_folder_writable_check(tmp_path):
    assert doctor._writable(tmp_path / "not" / "yet" / "made")  # created on first use
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert not doctor._writable(locked / "Notes")
    finally:
        locked.chmod(0o700)


def test_cli_exit_code(monkeypatch, capsys):
    from launcher.__main__ import main

    monkeypatch.setattr(doctor, "gather", lambda: healthy())
    assert main(["--doctor"]) == 0
    assert "Everything is set up." in capsys.readouterr().out
    monkeypatch.setattr(doctor, "gather", lambda: healthy(service="failed", daemon_running=False))
    assert main(["--doctor"]) == 1
    monkeypatch.setattr(doctor, "gather", lambda: healthy(gi_cairo=False))
    assert main(["--doctor"]) == 0  # warnings only


def test_long_shortcut_lists_are_shortened():
    missing = [f"Launcher: Mode {i} (Super+{i})" for i in range(5)]
    detail = by_key(healthy(shortcuts_missing=missing))["shortcuts"].detail
    assert detail == (
        "Not registered with GNOME: Launcher: Mode 0 (Super+0), Launcher: Mode 1 (Super+1), "
        "Launcher: Mode 2 (Super+2) and 2 more"
    )


def test_banner_shows_setup_problems_not_config_ones():
    facts = healthy(
        config_error="bad",  # the config banner shows this already
        shortcut_clashes=["x"],  # and this
        service_enabled=False,  # the launcher is running: not worth a banner
        gi_cairo=False,
        extension_state=doctor.EXT_INACTIVE,
    )
    checks = evaluate(facts)
    shown = doctor.launcher_problems(checks, set())
    assert [c.key for c in shown] == ["extension", "pdf"]  # errors first
    assert (
        doctor.banner_text(shown) == "Helper extension: Turned off: clipboard history and "
        "paste don't work (and 1 more)"
    )
    assert doctor.banner_text([]) == ""


def test_dismissing_hides_a_problem_until_it_changes_or_returns():
    off = evaluate(healthy(extension_state=doctor.EXT_INACTIVE))
    dismissed = {doctor.fingerprint(c) for c in doctor.launcher_problems(off, set())}
    assert doctor.launcher_problems(off, dismissed) == []
    failed = evaluate(healthy(extension_state=doctor.EXT_ERROR, extension_error="boom"))
    assert [c.key for c in doctor.launcher_problems(failed, dismissed)] == ["extension"]
    assert doctor.still_dismissed(off, dismissed) == dismissed
    assert doctor.still_dismissed(evaluate(healthy()), dismissed) == set()  # it went away
