"""The launcher window's result list, in a headless nested GNOME Shell: rows are built
in batches, a refresh keeps the selected entry, and how long a keystroke takes. Run:

    tools/nested-shell.sh .venv/bin/python tools/nested_window_test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from launcher.config import Config  # noqa: E402
from launcher.providers.base import Result  # noqa: E402
from launcher.window import FIRST_ROWS, LauncherWindow  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        context.iteration(False)
        time.sleep(0.005)


class FakeEngine:
    """Clipboard-like results: newest first, a copy can arrive while the list is open."""

    def __init__(self) -> None:
        self.clips = [f"clip {n}" for n in range(300, 0, -1)]
        self.calls = 0

    def query(self, text: str, mode: str, limit: int) -> list[Result]:
        self.calls += 1
        matches = [c for c in self.clips if text in c][:limit]
        return [
            Result(id=f"clip:{c}", title=c, subtitle="app · 1m ago", score=1 - i * 1e-4,
                   preview=("text", c * 20), key_actions={"delete": lambda: None})
            for i, c in enumerate(matches)
        ]  # fmt: skip

    def record(self, _result) -> None:
        pass


class App(Adw.Application):
    def mode_status(self, _mode: str) -> None:
        return None

    def notify_error(self, title: str, body: str) -> None:
        print("error:", title, body)


def rows(window: LauncherWindow) -> list[str]:
    out, n = [], 0
    while (row := window._list.get_row_at_index(n)) is not None:
        out.append(row.result.title)
        n += 1
    return out


def main() -> int:
    app = App(application_id="io.github.jinwei.LauncherWindowTest")
    app.register(None)
    engine = FakeEngine()
    window = LauncherWindow(app, engine, Config())
    window.show_mode("clipboard")
    pump(0.5)

    # A keystroke builds a screenful at once and the rest while idle.
    start = time.perf_counter()
    window.set_query("clip")  # "changed" -> _refresh
    elapsed = (time.perf_counter() - start) * 1000
    built_now = len(rows(window))
    check("a keystroke builds the first rows at once", built_now == FIRST_ROWS, str(built_now))
    print(f"      keystroke with 200 results: {elapsed:.1f} ms")
    pump(0.5)
    check("…and the rest right after", len(rows(window)) == 200, str(len(rows(window))))

    # Typing again before the rest is built: no stale rows from the old query.
    window.set_query("clip 2")
    window.set_query("clip 29")
    pump(0.5)
    shown = rows(window)
    check("a new query drops the rows still to come", all("29" in t for t in shown)
          and len(shown) == len(set(shown)), repr(shown[:5]))  # fmt: skip

    # A new copy arrives while an entry further down is selected.
    window.set_query("")
    pump(0.5)
    target = window._list.get_row_at_index(120)
    window._list.select_row(target)
    engine.clips.insert(0, "clip 999")
    window.refresh_if_visible()
    pump(0.3)
    selected = window._list.get_selected_row()
    check("a refresh keeps the selected entry selected",
          selected is not None and selected.result.title == "clip 180",
          selected and selected.result.title)  # fmt: skip
    check("…with every row built, so it can be found", len(rows(window)) == 200)

    window.close()
    print(f"{'all' if not FAILED else len(FAILED)} {'checks passed' if not FAILED else 'failed'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
