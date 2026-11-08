import os
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from launcher.keyboards import (  # noqa: E402
    KeyboardWatcher,
    espanso_started,
    is_keyboard,
    keyboards,
    parse_bitmap,
)

# Bitmaps copied from a real laptop (64-bit longs).
LAPTOP = "402000007 ff803078f800d001 feffffdfffcfffff fffffffffffffffe"
USB_KEYBOARD = "fab0ffdf77cfffff fffffffffffffffe"
CONSUMER_CONTROL = "3f00733fff 0 0 483ffff17aff32d bfd4444600000000 1 130ff38b17d000 10000002"
MOUSE = "1f0000 0 0 0 0"
ESPANSO = " ".join(["ffffffffffffffff"] * 3 + ["fffffffffffffffe"])


def add_device(root: Path, node: str, name: str, keys: str, parent: str = "pci/usb1") -> None:
    """A /sys/class/input/eventN symlink into /sys/devices, as the kernel makes it."""
    real = root / "devices" / parent / f"input{node[5:]}" / node
    (real / "device" / "capabilities").mkdir(parents=True)
    (real / "device" / "name").write_text(name + "\n")
    (real / "device" / "capabilities" / "key").write_text(keys + "\n")
    (root / "class" / "input").mkdir(parents=True, exist_ok=True)
    (root / "class" / "input" / node).symlink_to(real)


def test_parse_bitmap_puts_the_last_word_lowest():
    assert parse_bitmap("1 0") == 1 << 64
    assert parse_bitmap("fffffffffffffffe") >> 1 & 1
    assert parse_bitmap("0") == 0


def test_keyboards_are_devices_with_every_letter(tmp_path):
    add_device(tmp_path, "event3", "AT Translated Set 2 keyboard", LAPTOP, "platform/i8042")
    add_device(tmp_path, "event20", "RK68 Keyboard", USB_KEYBOARD)
    add_device(tmp_path, "event17", "RK68 Consumer Control", CONSUMER_CONTROL)
    add_device(tmp_path, "event21", "RK68 Mouse", MOUSE)
    sys_input = tmp_path / "class" / "input"
    assert is_keyboard("event3", sys_input)
    assert is_keyboard("event20", sys_input)
    assert not is_keyboard("event17", sys_input)  # media keys only
    assert not is_keyboard("event21", sys_input)
    assert not is_keyboard("event99", sys_input)  # gone already
    assert not is_keyboard("mouse0", sys_input)


def test_uinput_keyboards_are_left_out_but_bluetooth_le_counts(tmp_path):
    # espanso makes this one each time it starts: counting it would restart it forever
    add_device(tmp_path, "event12", "Espanso virtual device", ESPANSO, "virtual/input")
    add_device(tmp_path, "event30", "BLE keyboard", USB_KEYBOARD, "virtual/misc/uhid/0005:1")
    sys_input = tmp_path / "class" / "input"
    assert not is_keyboard("event12", sys_input)
    assert is_keyboard("event30", sys_input)


def test_keyboards_lists_names_and_when_they_appeared(tmp_path):
    add_device(tmp_path, "event3", "Laptop keyboard", LAPTOP, "platform/i8042")
    add_device(tmp_path, "event21", "Mouse", MOUSE)
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "event3").touch()
    os.utime(dev / "event3", (1000, 1000))
    found = keyboards(tmp_path / "class" / "input", dev)
    assert [(k.node, k.name, k.since) for k in found] == [("event3", "Laptop keyboard", 1000)]
    assert keyboards(tmp_path / "missing", dev) == []


def test_espanso_started_is_when_its_virtual_keyboard_appeared(tmp_path):
    add_device(tmp_path, "event3", "Laptop keyboard", LAPTOP, "platform/i8042")
    add_device(tmp_path, "event12", "Espanso virtual device", ESPANSO, "virtual/input")
    dev = tmp_path / "dev"
    dev.mkdir()
    for node, when in (("event3", 1000), ("event12", 2000)):
        (dev / node).touch()
        os.utime(dev / node, (when, when))
    sys_input = tmp_path / "class" / "input"
    assert espanso_started(sys_input, dev) == 2000
    (dev / "event12").unlink()
    assert espanso_started(sys_input, dev) == 0
    assert espanso_started(tmp_path / "missing", dev) == 0


def run_until(condition, timeout_s=5.0):
    """Run the main loop until condition() holds or the time is up."""
    expired = []
    source = GLib.timeout_add(int(timeout_s * 1000), lambda: expired.append(1))
    context = GLib.MainContext.default()
    while not condition() and not expired:
        context.iteration(True)
    if not expired:
        GLib.source_remove(source)
    return condition()


def test_watcher_reports_a_new_keyboard_once_per_burst(tmp_path):
    dev = tmp_path / "dev"
    dev.mkdir()
    calls = []
    watcher = KeyboardWatcher(calls.append, tmp_path / "class" / "input", dev, settle_ms=200)
    try:
        # A USB keyboard shows up as several devices; only some of them type.
        for node, name, keys in (
            ("event20", "RK68 Keyboard", USB_KEYBOARD),
            ("event21", "RK68 Mouse", MOUSE),
            ("event14", "RK68", LAPTOP),
        ):
            add_device(tmp_path, node, name, keys)
            (dev / node).touch()
        (dev / "mouse3").touch()  # not an event node
        assert run_until(lambda: calls)
        run_until(lambda: False, 0.4)  # nothing more arrives
        assert calls == [["RK68", "RK68 Keyboard"]]
    finally:
        watcher.shutdown()


def test_watcher_ignores_espanso_s_own_device_and_mice(tmp_path):
    dev = tmp_path / "dev"
    dev.mkdir()
    calls = []
    watcher = KeyboardWatcher(calls.append, tmp_path / "class" / "input", dev, settle_ms=100)
    try:
        add_device(tmp_path, "event12", "Espanso virtual device", ESPANSO, "virtual/input")
        add_device(tmp_path, "event21", "Mouse", MOUSE)
        (dev / "event12").touch()
        (dev / "event21").touch()
        run_until(lambda: False, 0.5)
        assert calls == []
    finally:
        watcher.shutdown()
