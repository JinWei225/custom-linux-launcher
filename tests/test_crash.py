import json

import pytest

from launcher import crash
from launcher.crash import CrashReport, previous_run

US = 1_000_000


def entry(run: str, message, at: int, own_field: bool = False) -> dict:
    field = "INVOCATION_ID" if own_field else "_SYSTEMD_INVOCATION_ID"
    return {field: run, "MESSAGE": message, "__REALTIME_TIMESTAMP": str(at * US)}


def test_previous_run_is_the_one_before_this_one(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    import time

    time.tzset()
    entries = [
        entry("old", "an older run", 100),
        entry("crashed", "launcher started", 3600),
        entry("crashed", "Traceback (most recent call last):", 3601),
        entry("crashed", "Main process exited, code=dumped, status=6/ABRT", 3602, own_field=True),
        entry("now", "launcher started", 3700),
    ]
    assert previous_run(entries, "now") == [
        "01:00:00 launcher started",
        "01:00:01 Traceback (most recent call last):",
        "01:00:02 Main process exited, code=dumped, status=6/ABRT",
    ]


def test_previous_run_edge_cases():
    assert previous_run([], "now") == []
    assert previous_run([entry("now", "only us", 1)], "now") == []
    many = [entry("crashed", f"line {i}", i) for i in range(100)]
    lines = previous_run(many + [entry("now", "x", 200)], "now", limit=5)
    assert [line.split(" ", 1)[1] for line in lines] == [f"line {i}" for i in range(95, 100)]
    binary = [{"_SYSTEMD_INVOCATION_ID": "crashed", "MESSAGE": list(b"caf\xc3\xa9 \xff")}]
    assert previous_run(binary, "now")[0].endswith("café �")
    assert previous_run(binary, "now")[0].startswith("--:--:--")


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


def test_report_round_trip(state):
    assert crash.load() is None
    report = CrashReport(1234.5, 2, ["a", "b"])
    crash.save(report)
    assert crash.load() == report
    crash.clear()
    assert crash.load() is None
    crash.clear()  # nothing to clear: fine
    crash.report_file().write_text("{broken")
    assert crash.load() is None


def test_detect(state, monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(crash, "restarts", lambda: 1)
    assert crash.detect() is None  # not run by systemd (make dev)

    monkeypatch.setenv("INVOCATION_ID", "now")
    monkeypatch.setattr(crash, "restarts", lambda: 0)
    assert crash.detect() is None  # started normally, or restarted by hand

    monkeypatch.setattr(crash, "restarts", lambda: 1)
    monkeypatch.setattr(
        crash, "journal", lambda: [entry("crashed", "boom", 5), entry("now", "hi", 6)]
    )
    report = crash.detect(now=99.0)
    assert (report.time, report.restarts) == (99.0, 1)
    assert report.lines[0].endswith("boom")
    assert json.loads(crash.report_file().read_text())["restarts"] == 1
