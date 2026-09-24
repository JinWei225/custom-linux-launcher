"""Keeps a FileIndex current: initial scan in a thread, then inotify via Gio.FileMonitor.

Gio directory monitors are not recursive, so there is one per indexed folder. Any event
in a folder schedules a debounced `rescan_dir` of that folder, which then reports the
subfolders to start or stop watching.
"""

from __future__ import annotations

import logging
import threading

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .files_index import MAX_WATCHED_DIRS, FileIndex, IndexSettings, scan  # noqa: E402

log = logging.getLogger(__name__)

RESCAN_DELAY_MS = 300  # coalesce bursts (downloads, archive extraction, git checkouts)


class FileWatcher:
    def __init__(self, index: FileIndex) -> None:
        self._index = index
        self._monitors: dict[str, Gio.FileMonitor] = {}
        self._pending: set[str] = set()
        self._rescan_source = 0
        self._generation = 0  # bumps on every reconfigure; stale scans are dropped
        self._settings: IndexSettings | None = None
        self._warned_limit = False

    def configure(self, settings: IndexSettings) -> None:
        """(Re)index from scratch when the folders or filters change."""
        if settings == self._settings:
            return
        self._settings = settings
        self._generation += 1
        generation = self._generation
        self._stop_all()

        def worker() -> None:
            state = scan(settings)
            GLib.idle_add(self._install, generation, settings, state)

        threading.Thread(target=worker, name="file-index-scan", daemon=True).start()

    def shutdown(self) -> None:
        self._generation += 1
        self._stop_all()

    # --- main thread -------------------------------------------------------------------

    def _install(self, generation: int, settings: IndexSettings, state) -> bool:
        if generation != self._generation:
            return GLib.SOURCE_REMOVE  # settings changed while scanning
        self._index.install(settings, state)
        self._watch(self._index.directories)
        log.info("watching %d folders for file search", len(self._monitors))
        return GLib.SOURCE_REMOVE

    def _watch(self, directories: list[str]) -> None:
        for directory in directories:
            if directory in self._monitors:
                continue
            if len(self._monitors) >= MAX_WATCHED_DIRS:
                if not self._warned_limit:
                    log.warning(
                        "watching the maximum of %d folders; changes deeper in will be missed "
                        "until the launcher restarts (lower [files] max_depth or add excludes)",
                        MAX_WATCHED_DIRS,
                    )
                    self._warned_limit = True
                return
            try:
                monitor = Gio.File.new_for_path(directory).monitor_directory(
                    Gio.FileMonitorFlags.WATCH_MOVES, None
                )
            except GLib.Error as e:
                log.warning("cannot watch %s: %s", directory, e.message)
                continue
            monitor.connect("changed", self._on_changed, directory)
            self._monitors[directory] = monitor

    def _unwatch(self, directories: list[str]) -> None:
        for directory in directories:
            monitor = self._monitors.pop(directory, None)
            if monitor is not None:
                monitor.cancel()

    def _stop_all(self) -> None:
        self._unwatch(list(self._monitors))
        self._pending.clear()
        if self._rescan_source:
            GLib.source_remove(self._rescan_source)
            self._rescan_source = 0

    def _on_changed(self, _monitor, _file, _other, event, directory: str) -> None:
        if event in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.PRE_UNMOUNT):
            return
        self._pending.add(directory)
        if not self._rescan_source:
            self._rescan_source = GLib.timeout_add(RESCAN_DELAY_MS, self._flush)

    def _flush(self) -> bool:
        self._rescan_source = 0
        pending, self._pending = self._pending, set()
        for directory in sorted(pending):  # parents before children
            added, removed = self._index.rescan_dir(directory)
            self._unwatch(removed)
            self._watch(added)
        return GLib.SOURCE_REMOVE
