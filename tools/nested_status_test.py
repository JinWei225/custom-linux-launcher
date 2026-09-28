"""The setup check in a headless nested GNOME Shell: `launcher --doctor` and Launcher
Settings → Status, against the nested shell's real extension state.

Turns the helper extension off, checks both report it with a fix, presses Fix (which
runs `gnome-extensions enable`) and checks it recovers; then the Copy Command button,
the page's attention flag and `--edit status`. Run through:

    tools/nested-shell.sh .venv/bin/python tools/nested_status_test.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from launcher import doctor, paths  # noqa: E402
from launcher.settings.window import SettingsWindow  # noqa: E402

LAUNCHER = str(ROOT / ".venv/bin/launcher")
UUID = doctor.EXTENSION_UUID
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    sys.stdout.flush()


def pump(seconds: float) -> None:
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def wait(predicate, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        pump(0.05)
    return predicate()


def doctor_cli() -> tuple[int, str]:
    out = subprocess.run([LAUNCHER, "--doctor"], capture_output=True, text=True, timeout=30)
    return out.returncode, out.stdout


def extension_line(text: str) -> str:
    return next((line for line in text.splitlines() if "Helper extension" in line), "")


def rows(page) -> dict[str, Adw.ActionRow]:
    return {row.get_title(): row for row in page._rows}


def buttons(row: Adw.ActionRow) -> dict[str, Gtk.Button]:
    found = {}
    widget = row.get_first_child()
    stack = [widget] if widget else []
    while stack:
        w = stack.pop()
        if isinstance(w, Gtk.Button) and w.get_label():
            found[w.get_label()] = w
        child = w.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


def main() -> int:
    notes = Path(os.environ["XDG_DATA_HOME"]) / "Notes"
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text(f'[notes]\nfolder = "{notes}"\n')

    code, text = doctor_cli()
    line = extension_line(text)
    check("--doctor sees the nested shell's extension", "Version 2 is active" in line, line)
    check("--doctor exits 1 only for serious problems",
          code == (1 if "✗" in text else 0), f"exit {code}")  # fmt: skip

    subprocess.run(["gnome-extensions", "disable", UUID], check=True)
    pump(1.0)
    code, text = doctor_cli()
    check("a turned-off extension is an error", "✗ Helper extension" in text and code == 1,
          extension_line(text))  # fmt: skip
    check("…with what to do", "Turn it on." in text)

    app = Adw.Application(application_id="io.github.jinwei.LauncherStatusTest")
    app.register(None)
    window = SettingsWindow(app)
    window.present()
    pump(0.8)
    window.open_item("status")
    pump(0.3)
    stack = window._stack
    check("--edit status opens the Status page", stack.get_visible_child_name() == "status")
    page = stack.get_visible_child()
    check("the page checks when shown", wait(lambda: bool(page.checks), 10))
    row = rows(page).get("Helper extension")
    check(
        "the extension row says it is off", row is not None and "Turned off" in row.get_subtitle()
    )
    check("…and the page asks for attention", stack.get_page(page).get_needs_attention())
    fix = buttons(row).get("Fix") if row else None
    check("…with a Fix button", fix is not None)
    if fix is not None:
        fix.emit("clicked")
        fixed = wait(lambda: "Version 2 is active" in rows(page)["Helper extension"].get_subtitle(),
                     10)  # fmt: skip
        check("Fix turns it back on", fixed, rows(page)["Helper extension"].get_subtitle())
        check("…and the attention flag goes",
              not stack.get_page(page).get_needs_attention())  # fmt: skip
    code, text = doctor_cli()
    check("--doctor agrees", "Version 2 is active" in extension_line(text), extension_line(text))

    # A problem you fix yourself (sudo): a Copy Command button, no Fix button.
    real_gather = page._gather
    page._gather = lambda: replace(real_gather(), gi_cairo=False)
    page.run_checks()
    wait(lambda: "python3-gi-cairo" in rows(page)["Notes PDF export"].get_subtitle(), 10)
    pdf = buttons(rows(page)["Notes PDF export"])
    check("a sudo fix offers Copy Command only", set(pdf) == {"Copy Command"}, repr(set(pdf)))
    if "Copy Command" in pdf:
        pdf["Copy Command"].emit("clicked")
        pump(0.2)
        clipboard = Gdk.Display.get_default().get_clipboard()
        got = []
        clipboard.read_text_async(None, lambda c, r: got.append(c.read_text_finish(r)))
        wait(lambda: bool(got), 3)
        check("…which copies the command", got == ["sudo apt install python3-gi-cairo"],
              repr(got))  # fmt: skip
    page._gather = real_gather

    window.close()
    pump(0.3)
    passed = sum(ok for _, ok in results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
