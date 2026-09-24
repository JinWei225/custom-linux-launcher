"""End-to-end clipboard history test inside a headless nested GNOME Shell.

Runs the real launcher daemon (dev venv) with the Launcher Helper extension, a GTK test
app that copies things like a normal app, and checks recording, previews, pasting back
into the app, pausing and terminal-style paste. Run through:

    tools/nested-shell.sh .venv/bin/python tools/nested_e2e_test.py [snapshot dir]
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = str(ROOT / ".venv/bin/launcher")
SNAPSHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
DATA = Path(os.environ["XDG_DATA_HOME"]) / "launcher"
CONFIG = Path(os.environ["XDG_CONFIG_HOME"]) / "launcher" / "config.toml"
TEST_APP_ID = "io.github.jinwei.LauncherNestedTest"

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))


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


def clips() -> list[dict]:
    path = DATA / "clipboard.db"
    if not path.exists():
        return []
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute("SELECT * FROM clips ORDER BY created DESC")]
    db.close()
    return rows


def wait_for(predicate, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


class TestApp:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "tools/nested_test_app.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        os.set_blocking(self.proc.stdout.fileno(), False)
        self.events: list[dict] = []

    def send(self, line: str) -> None:
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def read(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            line = self.proc.stdout.readline()
            if line:
                self.events.append(json.loads(line))
            else:
                time.sleep(0.02)

    def texts(self) -> list[str]:
        return [e["text"] for e in self.events if e["event"] == "text"]


def main() -> int:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    daemon_log = open(Path(os.environ["XDG_CACHE_HOME"]) / "daemon.log", "w")
    daemon = subprocess.Popen(
        [LAUNCHER, "--daemon", "--debug"], stdout=daemon_log, stderr=daemon_log
    )
    try:
        return run(daemon_log.name)
    finally:
        launcher("--quit")
        try:
            daemon.wait(5)
        except subprocess.TimeoutExpired:
            daemon.kill()


def run(daemon_log: str) -> int:
    time.sleep(2.5)
    log_text = Path(daemon_log).read_text()
    check("daemon sees the helper extension", "Launcher Helper extension v1 is active" in log_text)

    app = TestApp()
    app.read(4)
    check("test app is focused", any(e["event"] == "ready" for e in app.events))

    # 1. Text copied in an app is recorded with its source.
    app.send("copy first entry from the app")
    ok = wait_for(lambda: any(c["text"] == "first entry from the app" for c in clips()))
    check("copied text is recorded", ok, repr(clips()[:2]))
    if ok:
        clip = next(c for c in clips() if c["text"] == "first entry from the app")
        check("source app is remembered", TEST_APP_ID in clip["source"], clip["source"])

    # Mutter only accepts a new clipboard owner with a newer input serial. In a real
    # session every key press advances it; headless, nothing does. A paste through the
    # launcher sends real key events, so paste before each further copy.
    def paste_entry(query: str, snapshot: str | None = None) -> None:
        app.send("clear")
        app.read(0.3)
        launcher("--show", "--mode", "clipboard")
        time.sleep(0.8)
        action("debug-set-query", query)
        time.sleep(0.4)
        if SNAPSHOTS and snapshot:
            action("debug-snapshot", str(SNAPSHOTS / f"{snapshot}.png"))
            time.sleep(0.2)
        action("debug-run-selected")
        app.read(2.5)

    # 2. Paste an entry back into the app it was opened from.
    paste_entry("first entry", "clipboard-text")
    check(
        "Enter pastes into the app it was opened from",
        "first entry from the app" in app.texts(),
        repr(app.texts()[-3:]),
    )

    # 3. An image is recorded with thumbnails.
    png = Path(os.environ["XDG_CACHE_HOME"]) / "picture.png"
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1600, 900)
    pixbuf.fill(0x2E86DEFF)
    pixbuf.savev(str(png), "png", [], [])
    app.send(f"copy-image {png}")
    ok = wait_for(lambda: any(c["kind"] == "image" for c in clips()), 8)
    check("copied image is recorded", ok)
    if ok:
        image = next(c for c in clips() if c["kind"] == "image")
        files = sorted(p.name.split(".", 1)[1] for p in (DATA / "clips").iterdir())
        check("image size and files", (image["width"], image["height"]) == (1600, 900)
              and files == ["data", "icon.png", "preview.png"], f"{image} {files}")  # fmt: skip

    paste_entry("image", "clipboard-image")  # also advances the serial
    app.send("copy second entry")
    check(
        "a later copy is recorded",
        wait_for(lambda: any(c["text"] == "second entry" for c in clips())),
    )
    order = [c["text"] or "<image>" for c in clips()]
    check("newest first", order[:1] == ["second entry"], repr(order))

    # 4. Pause: nothing is recorded until resumed.
    paste_entry("second entry")
    launcher("--clipboard-pause")
    time.sleep(0.5)
    app.send("copy copied while paused")
    time.sleep(1.5)
    check("pause stops recording", all(c["text"] != "copied while paused" for c in clips()))
    launcher("--clipboard-pause")
    time.sleep(0.5)
    if SNAPSHOTS:
        launcher("--show", "--mode", "clipboard")
        time.sleep(0.6)
        action("debug-snapshot", str(SNAPSHOTS / "clipboard-list.png"))
        launcher("--hide")
        time.sleep(0.3)

    # 5. Terminals get Ctrl+Shift+V.
    text = CONFIG.read_text()
    CONFIG.write_text(
        text.replace("terminal_apps = [", f'terminal_apps = [\n  "{TEST_APP_ID}",', 1)
    )
    time.sleep(1.0)
    app.events.clear()
    paste_entry("second entry")
    keys = [e for e in app.events if e["event"] == "key" and e["keyval"] in ("v", "V")]
    check("terminal apps get Ctrl+Shift+V", any(k["ctrl"] and k["shift"] for k in keys), repr(keys))

    # 6. Deleting history the way Launcher Settings does (D-Bus action, seconds; 0 = all).
    first = next(c for c in clips() if c["text"] == "first entry from the app")
    db = sqlite3.connect(DATA / "clipboard.db")
    db.execute("UPDATE clips SET created = created - 7200 WHERE id = ?", (first["id"],))
    db.execute("UPDATE clips SET pinned = 1 WHERE kind = 'image'")
    db.commit()
    db.close()
    before = len(clips())
    if SNAPSHOTS:
        launcher("--show", "--mode", "clipboard")
        time.sleep(0.8)
        action("debug-snapshot", str(SNAPSHOTS / "clipboard-pinned.png"))
        time.sleep(0.3)
        launcher("--hide")
        time.sleep(0.3)
    subprocess.run(
        ["gdbus", "call", "--session", "--dest", "io.github.jinwei.Launcher",
         "--object-path", "/io/github/jinwei/Launcher", "--method",
         "org.gtk.Actions.Activate", "'clear-clipboard'", "[<int64 3600>]", "@a{sv} {}"],
        check=False, capture_output=True, timeout=10,
    )  # fmt: skip
    time.sleep(0.5)
    left = {c["text"] or "<image>" for c in clips()}
    check(
        "delete the last hour keeps older and pinned entries",
        left == {"first entry from the app", "<image>"},
        f"{before} -> {sorted(left)}",
    )

    app.proc.terminate()
    log_text = Path(daemon_log).read_text()
    errors = [line for line in log_text.splitlines() if "Traceback" in line or " ERROR " in line]
    check("no errors in the daemon log", not errors, "\n".join(errors[-5:]))
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("--- daemon log tail ---")
        print("\n".join(log_text.splitlines()[-25:]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
