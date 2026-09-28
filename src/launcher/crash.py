"""Noticing that the launcher service crashed, and keeping its last log lines.

systemd restarts launcher.service when it fails (Restart=on-failure) and counts those
restarts in NRestarts; starting or restarting it by hand resets the count. So a daemon
that starts with NRestarts > 0 replaces one that crashed. It saves the crashed run's
last journal lines to crash.json (shown in Settings → Status) and notifies once.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass

from . import paths

log = logging.getLogger(__name__)

UNIT = "launcher.service"
MAX_LINES = 40


@dataclass(frozen=True)
class CrashReport:
    time: float  # when the crash was noticed
    restarts: int  # systemd's NRestarts
    lines: list[str]  # the crashed run's last log lines


def report_file():
    return paths.state_dir() / "crash.json"


# --- pure ------------------------------------------------------------------------------


def _message(entry: dict) -> str:
    message = entry.get("MESSAGE", "")
    if isinstance(message, list):  # journald sends non-UTF-8 text as a list of bytes
        message = bytes(message).decode("utf-8", "replace")
    return str(message)


def _invocation(entry: dict) -> str:
    return entry.get("_SYSTEMD_INVOCATION_ID") or entry.get("INVOCATION_ID") or ""


def previous_run(entries: list[dict], current: str, limit: int = MAX_LINES) -> list[str]:
    """The last lines logged by the run before `current` (the invocation id systemd gave
    this run), oldest first, as "HH:MM:SS message"; systemd's own lines about that run
    (the exit status) included."""
    runs = [_invocation(e) for e in entries]
    previous = next((r for r in reversed(runs) if r and r != current), None)
    if previous is None:
        return []
    lines = []
    for entry, run in zip(entries, runs, strict=True):
        if run != previous:
            continue
        try:
            stamp = time.strftime(
                "%H:%M:%S", time.localtime(int(entry["__REALTIME_TIMESTAMP"]) / 1e6)
            )
        except (KeyError, ValueError):
            stamp = "--:--:--"
        lines.append(f"{stamp} {_message(entry)}")
    return lines[-limit:]


# --- reading the system ----------------------------------------------------------------


def restarts() -> int:
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", UNIT, "-p", "NRestarts", "--value"],
            capture_output=True, text=True, timeout=5,
        ).stdout  # fmt: skip
        return int(out.strip() or 0)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return 0


def journal() -> list[dict]:
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", UNIT, "-b", "-o", "json", "-n", "400", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        ).stdout  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return []
    entries = []
    for line in out.splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def detect(now: float | None = None) -> CrashReport | None:
    """At startup: a report (also saved) if systemd restarted us after a crash."""
    current = os.environ.get("INVOCATION_ID", "")
    if not current:
        return None  # not started by systemd (make dev, or a test)
    count = restarts()
    if count <= 0:
        return None
    report = CrashReport(
        time.time() if now is None else now, count, previous_run(journal(), current)
    )
    save(report)
    log.warning(
        "the previous launcher run crashed (restart %d); details in %s", count, report_file()
    )
    return report


def save(report: CrashReport) -> None:
    try:
        report_file().parent.mkdir(parents=True, exist_ok=True)
        report_file().write_text(json.dumps(asdict(report), indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("cannot save the crash report: %s", e)


def load() -> CrashReport | None:
    try:
        data = json.loads(report_file().read_text(encoding="utf-8"))
        return CrashReport(
            float(data["time"]), int(data["restarts"]), [str(x) for x in data["lines"]]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear() -> None:
    report_file().unlink(missing_ok=True)
