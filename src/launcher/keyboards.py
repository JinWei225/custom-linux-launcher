"""Keyboards plugged in while espanso runs.

espanso reads the keyboards that exist when it starts and never looks again, so a
keyboard connected later (plugged in, paired, or re-created when the laptop wakes up)
types without expanding anything. `KeyboardWatcher` notices new keyboards in /dev/input
so the launcher can restart espanso; `keyboards()` lists them for the setup check.

A keyboard is an input device with every letter key. Virtual keyboards made through
uinput are left out: espanso makes one itself each time it starts, so counting it would
restart espanso forever (keyboards paired over Bluetooth LE go through uhid, not uinput,
so they still count).
"""

from __future__ import annotations

import logging
import os
import re
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SYS_INPUT = Path("/sys/class/input")
DEV_INPUT = Path("/dev/input")
ESPANSO_DEVICE = "Espanso virtual device"  # made by espanso's worker each time it starts
SETTLE_MS = 1500  # a keyboard shows up as several devices at once: restart espanso once
_EVENT_NODE = re.compile(r"event\d+")
_WORD_BITS = struct.calcsize("l") * 8  # sysfs prints capability bitmaps in longs
_LETTERS = (*range(16, 26), *range(30, 39), *range(44, 51))  # KEY_Q…P, KEY_A…L, KEY_Z…M


@dataclass(frozen=True)
class Keyboard:
    node: str  # "event20"
    name: str  # "BY Tech RK68 HE Keyboard"
    since: float  # when its /dev/input node was made (epoch seconds); 0 if unknown


def parse_bitmap(text: str) -> int:
    """A capability bitmap as sysfs prints it: hex longs, the most significant first."""
    value = 0
    for word in text.split():
        value = (value << _WORD_BITS) | int(word, 16)
    return value


def is_keyboard(node: str, sys_input: Path = SYS_INPUT) -> bool:
    if not _EVENT_NODE.fullmatch(node):
        return False
    path = sys_input / node
    if "/devices/virtual/input/" in os.path.realpath(path):
        return False  # uinput: espanso's own virtual keyboard, ydotool…
    try:
        keys = parse_bitmap((path / "device" / "capabilities" / "key").read_text())
    except (OSError, ValueError):
        return False
    return all(keys >> bit & 1 for bit in _LETTERS)


def device_name(node: str, sys_input: Path = SYS_INPUT) -> str:
    try:
        return (sys_input / node / "device" / "name").read_text().strip() or node
    except OSError:
        return node


def keyboards(sys_input: Path = SYS_INPUT, dev_input: Path = DEV_INPUT) -> list[Keyboard]:
    found = []
    try:
        nodes = sorted(p.name for p in sys_input.iterdir())
    except OSError:
        return []
    for node in nodes:
        if not is_keyboard(node, sys_input):
            continue
        try:
            since = (dev_input / node).stat().st_mtime
        except OSError:
            since = 0.0
        found.append(Keyboard(node, device_name(node, sys_input), since))
    return found


def espanso_started(sys_input: Path = SYS_INPUT, dev_input: Path = DEV_INPUT) -> float:
    """When espanso's worker last read the keyboards (epoch seconds), going by the
    virtual keyboard it makes at the same time; 0 if it has none. More exact than the
    service's start: the worker also restarts whenever its match files change."""
    try:
        nodes = [p.name for p in sys_input.iterdir() if _EVENT_NODE.fullmatch(p.name)]
    except OSError:
        return 0.0
    started = 0.0
    for node in nodes:
        if device_name(node, sys_input) != ESPANSO_DEVICE:
            continue
        try:
            started = max(started, (dev_input / node).stat().st_mtime)
        except OSError:
            pass
    return started


class KeyboardWatcher:
    """Calls `on_new(names)` on the main loop, once per burst, when keyboards are added."""

    def __init__(
        self,
        on_new: Callable[[list[str]], None],
        sys_input: Path = SYS_INPUT,
        dev_input: Path = DEV_INPUT,
        settle_ms: int = SETTLE_MS,
    ) -> None:
        from gi.repository import Gio

        self._on_new = on_new
        self._sys_input = sys_input
        self._settle_ms = settle_ms
        self._added: set[str] = set()
        self._source = 0
        self._monitor: Gio.FileMonitor | None = None
        try:
            directory = Gio.File.new_for_path(str(dev_input))
            self._monitor = directory.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            self._monitor.connect("changed", self._on_changed)
        except Exception as e:  # no inotify on /dev: keyboards plugged in later won't expand
            log.warning("can't watch %s for new keyboards: %s", dev_input, e)

    def shutdown(self) -> None:
        from gi.repository import GLib

        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0

    def _on_changed(self, _monitor, file, _other, event) -> None:
        from gi.repository import Gio, GLib

        if event != Gio.FileMonitorEvent.CREATED:
            return
        name = file.get_basename()
        if not _EVENT_NODE.fullmatch(name):
            return
        self._added.add(name)
        if self._source:
            GLib.source_remove(self._source)
        self._source = GLib.timeout_add(self._settle_ms, self._settled)

    def _settled(self) -> bool:
        from gi.repository import GLib

        self._source = 0
        added, self._added = sorted(self._added), set()
        new = [node for node in added if is_keyboard(node, self._sys_input)]
        if new:
            self._on_new([device_name(node, self._sys_input) for node in new])
        return GLib.SOURCE_REMOVE
