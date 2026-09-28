"""End-to-end clipboard history test inside a headless nested GNOME Shell.

Runs the real launcher daemon (dev venv) with the Launcher Helper extension, a GTK test
app that copies things like a normal app, and checks recording, previews, pasting back
into the app, pausing, terminal-style paste and snippets. Run through:

    tools/nested-shell.sh .venv/bin/python tools/nested_e2e_test.py [snapshot dir]
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = str(ROOT / ".venv/bin/launcher")
SNAPSHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
DATA = Path(os.environ["XDG_DATA_HOME"]) / "launcher"
CONFIG = Path(os.environ["XDG_CONFIG_HOME"]) / "launcher" / "config.toml"
SNIPPETS = CONFIG.with_name("snippets.toml")
ESPANSO = Path(os.environ["XDG_CONFIG_HOME"]) / "espanso" / "match" / "launcher.yml"
RATES = Path(os.environ["XDG_CACHE_HOME"]) / "launcher" / "rates.json"
TEST_APP_ID = "io.github.jinwei.LauncherNestedTest"
NOTES = Path(os.environ["XDG_DATA_HOME"]) / "Notes"
STATE = Path(os.environ["XDG_STATE_HOME"]) / "launcher"
UUID = "launcher-helper@jinwei.github.io"

results: list[tuple[str, bool]] = []
rates_written = 0.0  # "fetched" time of the rates.json seeded before the daemon starts


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


def window_open(title: str) -> bool:
    """Whether the nested shell has a window with this title (nested-input extension)."""
    out = subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
         "--object-path", "/io/github/jinwei/NestedInput", "--method",
         "io.github.jinwei.NestedInput.WindowFrame", title],
        check=False, capture_output=True, text=True, timeout=10,
    ).stdout  # fmt: skip
    return bool(out) and not out.startswith("([-1,")


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

    def last(self, event: str) -> dict | None:
        return next((e for e in reversed(self.events) if e["event"] == event), None)


def notes_call(*args: str) -> str:
    """Ask the running Notes app something over D-Bus ("" if it isn't running)."""
    return subprocess.run(
        ["gdbus", "call", "--session", "--dest", "io.github.jinwei.Launcher.Notes",
         "--object-path", "/io/github/jinwei/Launcher/Notes", *args],
        check=False, capture_output=True, text=True, timeout=10,
    ).stdout  # fmt: skip


def main() -> int:
    from launcher.config import DEFAULT_CONFIG_TEXT

    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    # The default config, but with notes in this session: the daemon starts Notes in
    # the background (preload), which must not read the real ~/Notes.
    CONFIG.write_text(DEFAULT_CONFIG_TEXT.replace('folder = "~/Notes"', f'folder = "{NOTES}"'))
    ESPANSO.parent.mkdir(parents=True, exist_ok=True)
    SNIPPETS.write_text(
        '[[snippet]]\nname = "Greeting"\ntrigger = ";hi"\n'
        'body = "Hello {clipboard}!{cursor} bye"\n\n'
        '[[snippet]]\nname = "Plain"\nbody = """\nplain snippet"""\n'
    )
    # Fixed exchange rates, fresh enough that the daemon doesn't download new ones.
    RATES.parent.mkdir(parents=True, exist_ok=True)
    RATES.write_text(json.dumps({"updated": time.time(), "fetched": time.time(),
                                 "rates": {"USD": 1, "MYR": 4, "JPY": 150}}))  # fmt: skip
    global rates_written
    rates_written = json.loads(RATES.read_text())["fetched"]
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
    # The daemon indexes files before it starts, which can take a few seconds.
    active = wait_for(
        lambda: "Launcher Helper extension v2 is active" in Path(daemon_log).read_text(),
        seconds=15,
    )
    check("daemon sees the helper extension", active)
    check(
        "espanso gets the snippets with a trigger",
        ESPANSO.exists() and ";hi" in ESPANSO.read_text() and "Plain" not in ESPANSO.read_text(),
    )

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

    # 2b. Snippets: placeholders, cursor, not recorded, clipboard put back afterwards.
    app.send("clear")
    app.read(0.3)
    launcher("--show", "--mode", "snippets")
    time.sleep(0.8)
    action("debug-set-query", "greet")
    time.sleep(0.4)
    if SNAPSHOTS:
        action("debug-snapshot", str(SNAPSHOTS / "snippets.png"))
        time.sleep(0.2)
    app.events.clear()
    action("debug-run-selected")
    app.read(2.5)
    expected = "Hello first entry from the app! bye"
    check("snippet pasted with {clipboard}", expected in app.texts(), repr(app.texts()[-3:]))
    cursor = app.last("cursor")
    check(
        "{cursor} puts the cursor back",
        cursor is not None and cursor["position"] == len(expected) - len(" bye"),
        repr(cursor),
    )
    check("pasted snippets are not recorded", all("Hello" not in c["text"] for c in clips()))
    app.send("read-clipboard")
    app.read(0.5)
    got = app.last("clipboard")
    check(
        "clipboard is put back after a snippet",
        got is not None and got.get("text") == "first entry from the app",
        repr(got),
    )
    app.send("clear")
    app.read(0.3)
    launcher("--run", "snippet:plain")  # what a snippet hotkey runs
    app.read(2.0)
    check("--run snippet:<name> pastes it", "plain snippet" in app.texts(), repr(app.texts()[-2:]))
    SNIPPETS.write_text(SNIPPETS.read_text().replace(";hi", ";hey"))
    time.sleep(1.0)
    check("editing snippets updates espanso", ";hey" in ESPANSO.read_text())

    # 2c. Converters: Alt+Enter pastes the answer, like a snippet (Enter only copies).
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    app.send("clear")
    app.read(0.3)
    launcher("--show", "--mode", "all")
    time.sleep(0.8)
    action("debug-set-query", "tomorrow")
    time.sleep(0.4)
    if SNAPSHOTS:
        action("debug-snapshot", str(SNAPSHOTS / "dates.png"))
        time.sleep(0.2)
    action("debug-run-selected-alt")
    app.read(2.5)
    check("Alt+Enter pastes a date answer", tomorrow in app.texts(), repr(app.texts()[-2:]))
    check("pasted answers are not recorded", all(c["text"] != tomorrow for c in clips()))
    app.send("read-clipboard")
    app.read(0.5)
    got = app.last("clipboard")
    check(
        "clipboard is put back after an answer",
        got is not None and got.get("text") == "first entry from the app",
        repr(got),
    )

    # 2d. Enter copies a timezone answer (in the nested shell's clock format).
    launcher("--show", "--mode", "all")
    time.sleep(0.8)
    action("debug-set-query", "time in utc")
    time.sleep(0.4)
    if SNAPSHOTS:
        action("debug-snapshot", str(SNAPSHOTS / "timezones.png"))
        time.sleep(0.2)
    action("debug-run-selected")
    time.sleep(0.5)
    app.send("read-clipboard")
    app.read(0.5)
    got = app.last("clipboard") or {}
    utc_now = datetime.now(UTC)
    expected = {f"{t.hour:02d}:{t.minute:02d}" for t in (utc_now, utc_now - timedelta(minutes=1))}
    check("Enter copies a timezone answer", got.get("text") in expected, repr(got))

    # 2e. Currency: rates from the cache file; Enter copies the plain amount.
    launcher("--show", "--mode", "all")
    time.sleep(0.8)
    action("debug-set-query", "1.5k usd to jpy")
    time.sleep(0.4)
    if SNAPSHOTS:
        action("debug-snapshot", str(SNAPSHOTS / "currency.png"))
        time.sleep(0.2)
    action("debug-run-selected")
    time.sleep(0.5)
    app.send("read-clipboard")
    app.read(0.5)
    got = app.last("clipboard") or {}
    check("Enter copies a currency answer", got.get("text") == "225000", repr(got))
    # Settings' "Refresh Now": the daemon downloads (or, offline, logs why it couldn't).
    action("refresh-rates")
    downloaded = wait_for(
        lambda: (
            json.loads(RATES.read_text())["fetched"] > rates_written
            or "exchange rates not available" in Path(daemon_log).read_text()
        ),
        seconds=15,
    )
    check("refresh-rates action downloads rates", downloaded)

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

    # 6b. Appearance: the running launcher follows [ui] appearance when the file changes.
    def background(appearance: str) -> tuple[int, int, int] | None:
        text = CONFIG.read_text()
        CONFIG.write_text(re.sub(r'appearance = "\w+"', f'appearance = "{appearance}"', text))
        time.sleep(1.0)
        launcher("--show", "--mode", "all")
        time.sleep(0.6)
        shot = Path(os.environ["XDG_CACHE_HOME"]) / f"appearance-{appearance}.png"
        action("debug-snapshot", str(shot))
        wait_for(shot.exists, 3)
        launcher("--hide")
        time.sleep(0.3)
        if not shot.exists():
            return None
        pixels = GdkPixbuf.Pixbuf.new_from_file(str(shot)).get_pixels()
        return tuple(pixels[:3])  # the top-left corner: window background

    dark = background("dark")
    check("appearance = dark turns the launcher dark", dark is not None and max(dark) < 80,
          repr(dark))  # fmt: skip
    light = background("system")
    check("appearance = system follows GNOME (light)", light is not None and min(light) > 200,
          repr(light))  # fmt: skip

    # 7. Notes are found by title; Enter opens the note in Notes (last: it takes focus).
    # The daemon started Notes hidden a few seconds after it started ([notes] preload).
    preloaded = wait_for(lambda: "toggle" in notes_call("--method", "org.gtk.Actions.List"), 20)
    check("the launcher starts Notes in the background", preloaded)
    check("…without showing it", not window_open("Notes"))
    NOTES.mkdir(parents=True, exist_ok=True)
    (NOTES / "Lecture 3.md").write_text("# Lecture 3: Fourier **Series**\n\nnotes\n")
    launcher("--show", "--mode", "all")
    time.sleep(0.8)
    action("debug-set-query", "fourier")
    time.sleep(0.4)
    if SNAPSHOTS:
        action("debug-snapshot", str(SNAPSHOTS / "notes-search.png"))
        time.sleep(0.2)
    action("debug-run-selected")
    opened = wait_for(lambda: window_open("Lecture 3: Fourier Series – Notes"), seconds=10)
    check("Enter on a note opens it in Notes", opened)
    notes_call("--method", "org.gtk.Actions.Activate", "'quit'", "@av []", "@a{sv} {}")
    time.sleep(0.5)

    # 8. Setup problems reach the launcher's banner, and can be dismissed.
    def launcher_height() -> int:
        shot = Path(os.environ["XDG_CACHE_HOME"]) / "banner.png"
        shot.unlink(missing_ok=True)
        launcher("--show", "--mode", "all")
        time.sleep(0.6)
        action("debug-snapshot", str(shot))
        wait_for(shot.exists, 3)
        if SNAPSHOTS:
            shutil.copy(shot, SNAPSHOTS / f"setup-banner-{len(results)}.png")
        launcher("--hide")
        time.sleep(0.3)
        return GdkPixbuf.Pixbuf.new_from_file(str(shot)).get_height() if shot.exists() else 0

    plain = launcher_height()
    subprocess.run(["gnome-extensions", "disable", UUID], check=False)
    reported = wait_for(
        lambda: "setup: Helper extension: Turned off" in Path(daemon_log).read_text(), 15
    )
    check("a setup problem is noticed (helper extension turned off)", reported)
    with_banner = launcher_height()
    check("…and shown in the launcher's banner", with_banner > plain + 20,
          f"{plain} -> {with_banner}")  # fmt: skip
    action("dismiss-setup-problems")
    dismissed = STATE / "dismissed.json"
    remembered = wait_for(lambda: dismissed.exists() and "extension:" in dismissed.read_text())
    check("dismissing remembers it", remembered)
    check("…and hides the banner", launcher_height() == plain)
    subprocess.run(["gnome-extensions", "enable", UUID], check=False)
    check("…and forgets it once it is fixed", wait_for(lambda: dismissed.read_text() == "[]", 15))

    # 9. The crash notice: a daemon that systemd restarted after a crash (faked with
    # systemctl and journalctl stand-ins and systemd's INVOCATION_ID).
    launcher("--quit")
    time.sleep(1.0)
    fake = Path(os.environ["XDG_CACHE_HOME"]) / "fakebin"
    fake.mkdir(exist_ok=True)
    (fake / "systemctl").write_text(
        '#!/bin/sh\ncase "$*" in *NRestarts*) echo 1; exit 0;; esac\nexec /usr/bin/systemctl "$@"\n'
    )
    entries = [
        {"_SYSTEMD_INVOCATION_ID": "crashed-run", "MESSAGE": "launcher started"},
        {"_SYSTEMD_INVOCATION_ID": "crashed-run", "MESSAGE": "KeyError: 'boom'"},
        {"INVOCATION_ID": "crashed-run", "MESSAGE": "Main process exited, status=6/ABRT"},
        {"_SYSTEMD_INVOCATION_ID": "this-run", "MESSAGE": "launcher started again"},
    ]
    journal = "\n".join(json.dumps(e | {"__REALTIME_TIMESTAMP": "1"}) for e in entries)
    (fake / "journalctl").write_text(f"#!/bin/sh\ncat <<'END'\n{journal}\nEND\n")
    for tool in ("systemctl", "journalctl"):
        (fake / tool).chmod(0o755)
    monitor = subprocess.Popen(
        ["dbus-monitor", "--session", "interface='org.gtk.Notifications'"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )  # fmt: skip
    time.sleep(0.5)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "INVOCATION_ID": "this-run"}
    restarted_log = open(Path(os.environ["XDG_CACHE_HOME"]) / "daemon2.log", "w")
    restarted = subprocess.Popen(
        [LAUNCHER, "--daemon", "--debug"], env=env, stdout=restarted_log, stderr=restarted_log
    )
    report = STATE / "crash.json"
    check("a restart after a crash is noticed", wait_for(report.exists, 10))
    lines = json.loads(report.read_text())["lines"] if report.exists() else []
    check("…keeping the crashed run's last log lines",
          [line.split(" ", 1)[1] for line in lines] == [
              "launcher started", "KeyError: 'boom'", "Main process exited, status=6/ABRT"],
          repr(lines))  # fmt: skip
    time.sleep(1.0)
    monitor.terminate()
    sent = monitor.communicate(timeout=5)[0]
    check("…with a notification", "The launcher crashed and was restarted" in sent)
    check("…whose button opens Status", "app.show-status" in sent)
    action("show-status")
    check("Show Details opens Launcher Settings",
          wait_for(lambda: window_open("Launcher Settings"), 10))  # fmt: skip
    subprocess.run(["pkill", "-f", "--", "--settings --edit status"], check=False)
    launcher("--quit")
    try:
        restarted.wait(5)
    except subprocess.TimeoutExpired:
        restarted.kill()

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
