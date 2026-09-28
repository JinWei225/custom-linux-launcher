"""Appearance (System / Light / Dark) in a headless nested GNOME Shell.

Notes follows a change written to config.toml by another process; Launcher Settings'
Style row writes the choice and applies it at once; "Follow System" tracks GNOME's
dark style. (The launcher daemon's side is in nested_e2e_test.py.) Run through:

    tools/nested-shell.sh .venv/bin/python tools/nested_appearance_test.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from launcher import paths  # noqa: E402
from launcher.config import load_config  # noqa: E402
from launcher.config_writer import ConfigWriter  # noqa: E402
from launcher.notes.window import NotesWindow  # noqa: E402
from launcher.settings.window import SettingsWindow  # noqa: E402

NOTES = Path(os.environ["XDG_DATA_HOME"]) / "Notes"
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


def wait(predicate, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        pump(0.05)
    return predicate()


def gnome_style(value: str) -> None:
    subprocess.run(
        ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", value], check=True
    )


def main() -> int:
    NOTES.mkdir(parents=True, exist_ok=True)
    (NOTES / "Test.md").write_text("# Colours\nsee https://gnome.org\n")
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text(f'[ui]\nwidth = 680\n\n[notes]\nfolder = "{NOTES}"\n')
    gnome_style("default")
    style = Adw.StyleManager.get_default()

    app = Adw.Application(application_id="io.github.jinwei.LauncherAppearanceTest")
    app.register(None)
    notes = NotesWindow(app)
    notes.present()
    pump(1.0)
    notes.open_note("Test.md")
    pump(0.3)
    check("starts light while GNOME is light", not style.get_dark())
    light_text = notes.editor.get_color()

    # Another process (Launcher Settings) writes the choice: Notes follows it.
    writer = ConfigWriter(paths.config_file())
    writer.set_value("ui", "appearance", "dark")
    check("Notes turns dark when the config says dark", wait(style.get_dark))
    pump(0.3)
    dark_text = notes.editor.get_color()
    check("…and its text colours follow", dark_text.red > 0.8 > light_text.red,
          f"{light_text.to_string()} -> {dark_text.to_string()}")  # fmt: skip
    writer.set_value("ui", "appearance", "system")
    check("…and back when it says system", wait(lambda: not style.get_dark()))

    # "Follow System" tracks GNOME's style setting (through the settings portal).
    gnome_style("prefer-dark")
    check("system: dark when GNOME is dark", wait(style.get_dark, 5))
    gnome_style("default")
    check("system: light again", wait(lambda: not style.get_dark(), 5))

    # A broken config keeps the current appearance.
    paths.config_file().write_text('[ui]\nappearance = "sepia"\n')
    pump(1.0)
    check("an invalid value changes nothing", not style.get_dark())
    notes.close()
    pump(0.3)

    # Launcher Settings: the Style row writes the file and applies straight away.
    writer.path.write_text(f'[ui]\nwidth = 680\n\n[notes]\nfolder = "{NOTES}"\n')
    settings = SettingsWindow(app)
    settings.present()
    pump(1.0)
    row = settings._pages[0]._appearance
    labels = [row.get_model().get_string(i) for i in range(row.get_model().get_n_items())]
    check("the Style row offers the three choices",
          labels == ["Follow System", "Light", "Dark"], repr(labels))  # fmt: skip
    check("…showing the current one", row.get_selected() == 0)
    row.set_selected(2)
    check("choosing Dark applies at once", style.get_dark())
    check("…and is saved", load_config(paths.config_file())[0].ui.appearance == "dark")
    check("…keeping the rest of the file", "width = 680" in paths.config_file().read_text())
    gnome_style("prefer-dark")
    row.set_selected(1)
    check("Light overrides a dark GNOME", wait(lambda: not style.get_dark()))
    row.set_selected(0)
    check("Follow System goes dark with GNOME", wait(style.get_dark))
    gnome_style("default")
    settings.close()
    pump(0.3)

    passed = sum(ok for _, ok in results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
