"""The README's screenshots, taken in a headless nested GNOME Shell with sample data.

Nothing personal ends up in them: the config, notes, clipboard history, exchange rates
and the folder file search looks in are all made up here. Run through:

    make screenshots
    (= tools/nested-shell.sh .venv/bin/python tools/nested_screenshots.py docs/screenshots)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, GdkPixbuf, GLib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from launcher import paths  # noqa: E402
from launcher.clipboard_store import ClipboardStore  # noqa: E402

LAUNCHER = str(ROOT / ".venv/bin/launcher")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
# A made-up home, so file search shows "~/Documents/…" rather than a temporary path.
HOME = Path(os.environ["XDG_DATA_HOME"]).parent / "home"
os.environ["HOME"] = str(HOME)  # for this process (Notes, Settings) and the daemon
NOTES = HOME / "Notes"
FILES = HOME / "Documents"

CONFIG = """\
[ui]
favicons = false

[files]
folders = ["~/Documents"]

[notes]
folder = "~/Notes"

[[quicklink]]
name = "Google"
alias = "g"
url = "https://www.google.com/search?q={query}"
fallback = true

[[quicklink]]
name = "GitHub"
alias = "gh"
url = "https://github.com/"
"""
SNIPPETS = """\
[[snippet]]
name = "Email signature"
trigger = ";sig"
body = \"\"\"
Best regards,
Alex Tan
Final-year student, Electrical Engineering\"\"\"

[[snippet]]
name = "Today's date"
trigger = ";date"
body = "{date:%d %B %Y}"

[[snippet]]
name = "Meeting reply"
body = "Thanks! {cursor} works for me, see you then."
"""
LECTURE = """\
# Lecture 3: Fourier Series

Periodic signals as sums of sinusoids.

## Key ideas
- Orthogonality of basis functions
\t- sin and cos
- Convergence (Dirichlet conditions)

## Steps
1. Find the period *T*
2. Compute a₀
3. Compute aₙ and bₙ

- [x] Read chapter 3
- [ ] Problem set 2

> Any periodic function can be written as a sum of sines.

**Exam tip:** check ==even/odd symmetry== first; see [the course page](https://example.edu/signals).

```python
x = np.fft.fft(signal)
```
"""
NOTE_FILES = {
    "Signals & Systems/Lecture 3 – Fourier Series.md": LECTURE,
    "Signals & Systems/Lecture 2 – LTI Systems.md": "# Lecture 2: LTI Systems\n\n- Convolution\n",
    "Signals & Systems/Tutorial answers.md": "# Tutorial answers\n\n1. 42\n",
    "FYP/Supervisor meeting.md": "# Supervisor meeting\n\n- [ ] Send the draft by Friday\n",
    "FYP/Literature review.md": "# Literature review\n",
    "Shopping list.md": "# Shopping list\n\n- [ ] Milk\n",
}
SAMPLE_FILES = [
    "Signals/Lecture 3 - Fourier Series.pdf",
    "Signals/Lecture 2 - LTI Systems.pdf",
    "Signals/Tutorial 3 solutions.pdf",
    "FYP/Literature review draft.docx",
    "Lecture timetable.png",
]


def launcher(*args: str) -> None:
    subprocess.run([LAUNCHER, *args], check=False, timeout=10)


def action(name: str, param: str | None = None) -> None:
    value = f"[<'{param}'>]" if param is not None else "@av []"
    subprocess.run(
        ["gdbus", "call", "--session", "--dest", "io.github.jinwei.Launcher",
         "--object-path", "/io/github/jinwei/Launcher", "--method",
         "org.gtk.Actions.Activate", f"'{name}'", value, "@a{sv} {}"],
        check=False, capture_output=True, timeout=10,
    )  # fmt: skip


def wait_for(predicate, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def pump(seconds: float) -> None:
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)


def png(width: int, height: int, colour: int) -> bytes:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(colour)
    return pixbuf.save_to_bufferv("png", [], [])[1]


def seed() -> None:
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text(CONFIG)
    paths.config_file().with_name("snippets.toml").write_text(SNIPPETS)
    paths.espanso_match_dir().mkdir(parents=True, exist_ok=True)  # as if espanso is set up
    for rel, text in NOTE_FILES.items():
        (NOTES / rel).parent.mkdir(parents=True, exist_ok=True)
        (NOTES / rel).write_text(text)
    (NOTES / ".notes.json").write_text(json.dumps({"pinned": ["FYP/Supervisor meeting.md"]}))
    for i, rel in enumerate(SAMPLE_FILES):
        (FILES / rel).parent.mkdir(parents=True, exist_ok=True)
        (FILES / rel).write_bytes(b"sample")
        os.utime(FILES / rel, (time.time() - i * 5400, time.time() - i * 5400))
    paths.rates_file().parent.mkdir(parents=True, exist_ok=True)
    paths.rates_file().write_text(json.dumps({
        "updated": time.time(), "fetched": time.time(),
        "rates": {"USD": 1, "MYR": 4.21, "JPY": 149.8, "EUR": 0.92, "SGD": 1.29},
    }))  # fmt: skip
    clips = ClipboardStore(paths.data_dir())
    now = time.time()
    clips.add_text("https://example.edu/signals/week3", "firefox_firefox", now - 3000)
    clips.add_image(png(1600, 900, 0x3584E4FF), "image/png", "org.gnome.Nautilus", 1600, 900,
                    png(64, 36, 0x3584E4FF), png(480, 270, 0x3584E4FF), now - 2000)  # fmt: skip
    clips.add_text('git commit -m "Add Fourier notes"', "org.gnome.Ptyxis", now - 900)
    clips.add_text(
        "The Fourier series of a periodic signal x(t) with period T is\n"
        "x(t) = a₀ + Σ (aₙ cos(nω₀t) + bₙ sin(nω₀t))",
        "org.gnome.TextEditor", now - 60,
    )  # fmt: skip
    clips.close()


def launcher_shots() -> None:
    log = open(Path(os.environ["XDG_CACHE_HOME"]) / "daemon.log", "w")
    daemon = subprocess.Popen([LAUNCHER, "--daemon", "--debug"], stdout=log, stderr=log)
    wait_for(lambda: "launcher started" in Path(log.name).read_text(), 15)
    time.sleep(3.0)  # the file index and the setup check

    def shot(mode: str, query: str, name: str) -> None:
        launcher("--show", "--mode", mode)
        time.sleep(0.7)
        action("debug-set-query", query)
        time.sleep(0.6)
        path = OUT / f"{name}.png"
        path.unlink(missing_ok=True)
        action("debug-snapshot", str(path))
        wait_for(path.exists, 5)
        launcher("--hide")
        time.sleep(0.4)
        print(("saved " if path.exists() else "MISSING ") + path.name)

    try:
        shot("all", "fourier", "launcher-notes")
        shot("all", "one week after 26 october 2026", "launcher-dates")
        shot("all", "3pm tokyo to london", "launcher-timezones")
        shot("all", "100 usd to myr", "launcher-currency")
        shot("files", "lecture", "launcher-files")
        shot("clipboard", "", "launcher-clipboard")
        shot("snippets", "", "launcher-snippets")
    finally:
        launcher("--quit")
        try:
            daemon.wait(5)
        except subprocess.TimeoutExpired:
            daemon.kill()


def window_shots() -> None:
    from launcher.config_writer import ConfigWriter
    from launcher.notes.window import NotesWindow
    from launcher.settings.debug import snapshot
    from launcher.settings.window import SettingsWindow

    app = Adw.Application(application_id="io.github.jinwei.LauncherScreenshots")
    app.register(None)

    notes = NotesWindow(app)
    notes.set_default_size(1100, 760)
    notes.present()
    pump(1.0)
    notes.open_note("Signals & Systems/Lecture 3 – Fourier Series.md")
    notes.buffer.place_cursor(notes.buffer.get_end_iter())
    pump(1.0)
    notes.editor.scroll_to_iter(notes.buffer.get_start_iter(), 0, False, 0, 0)
    pump(0.5)
    snapshot(notes, str(OUT / "notes.png"))
    ConfigWriter(paths.config_file()).set_value("ui", "appearance", "dark")
    pump(1.5)
    snapshot(notes, str(OUT / "notes-dark.png"))
    ConfigWriter(paths.config_file()).set_value("ui", "appearance", "system")
    pump(1.0)
    notes.close()
    pump(0.5)

    settings = SettingsWindow(app)
    settings.present()
    pump(1.0)
    for page in ("general", "status"):
        settings._stack.set_visible_child_name(page)
        pump(3.0 if page == "status" else 0.8)  # the setup check runs when it is shown
        snapshot(settings, str(OUT / f"settings-{page}.png"))
    settings.close()
    pump(0.3)
    for name in ("notes", "notes-dark", "settings-general", "settings-status"):
        print(("saved " if (OUT / f"{name}.png").exists() else "MISSING ") + f"{name}.png")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    seed()
    launcher_shots()
    window_shots()
    return 0


if __name__ == "__main__":
    sys.exit(main())
