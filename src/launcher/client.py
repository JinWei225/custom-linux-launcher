"""Fast path for `launcher` invocations while the daemon is already running.

Importing PyGObject costs ~140 ms, which is most of the time budget for opening the
window. Instead, this asks the running instance to activate one of its actions through
the org.gtk.Actions DBus interface that Gio.Application exports, using the `gdbus`
CLI. No gi import happens on this path.
"""

from __future__ import annotations

import enum
import os
import subprocess
from collections.abc import Mapping

from . import APP_ID, OBJECT_PATH

TIMEOUT_SECONDS = 3


class SendStatus(enum.Enum):
    SENT = "sent"
    NOT_RUNNING = "not-running"  # no daemon (or gdbus missing): caller starts one
    TIMEOUT = "timeout"  # daemon owns the name but is not answering


def gvariant_string(value: str) -> str:
    """Quote a string as GVariant text."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_command(action: str, param: str | None, env: Mapping[str, str]) -> list[str]:
    # Forward the activation token so GNOME lets the window take focus.
    platform = []
    if token := env.get("XDG_ACTIVATION_TOKEN"):
        platform.append(f"'activation-token': <{gvariant_string(token)}>")
    if startup_id := env.get("DESKTOP_STARTUP_ID"):
        platform.append(f"'desktop-startup-id': <{gvariant_string(startup_id)}>")
    platform_data = "{" + ", ".join(platform) + "}" if platform else "@a{sv} {}"
    params = f"[<{gvariant_string(param)}>]" if param is not None else "@av []"
    return [
        "gdbus", "call", "--session",
        "--dest", APP_ID,
        "--object-path", OBJECT_PATH,
        "--method", "org.gtk.Actions.Activate",
        gvariant_string(action), params, platform_data,
    ]  # fmt: skip


def send(action: str, param: str | None) -> SendStatus:
    cmd = build_command(action, param, os.environ)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_SECONDS)
    except FileNotFoundError:
        return SendStatus.NOT_RUNNING
    except subprocess.TimeoutExpired:
        return SendStatus.TIMEOUT
    return SendStatus.SENT if proc.returncode == 0 else SendStatus.NOT_RUNNING
