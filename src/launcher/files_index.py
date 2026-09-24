"""In-memory index of the files under the configured folders.

Pure Python (no GTK) so it can be tested on a temporary directory. The initial scan
(`scan`) is a plain function that can run in a worker thread; the resulting
IndexState is installed on the main thread, which then owns it exclusively. Watchers
report "something changed in directory D" and the index re-reads just D (`rescan_dir`),
which is robust against the details of individual create/move/delete events.
"""

from __future__ import annotations

import bisect
import fnmatch
import logging
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .ranking import fuzzy_score

log = logging.getLogger(__name__)

# Recently modified files rank higher: score * (1 + RECENCY_WEIGHT * 0.5 ** (age / half-life))
RECENCY_WEIGHT = 0.3
RECENCY_HALF_LIFE_DAYS = 7.0
# Safety limits so a huge folder (or a mistake like folders = ["~"]) cannot eat the
# machine: indexing stops at MAX_ENTRIES files, watching at MAX_WATCHED_DIRS folders.
MAX_ENTRIES = 200_000
MAX_WATCHED_DIRS = 20_000


@dataclass(frozen=True)
class IndexSettings:
    roots: tuple[str, ...]  # absolute, ~ already expanded
    exclude: tuple[str, ...] = ()
    max_depth: int = 8
    show_hidden: bool = False

    @classmethod
    def from_config(cls, files_config) -> IndexSettings:
        roots = []
        home = os.path.expanduser("~")
        for folder in files_config.folders:
            # Relative folders ("Desktop") are relative to home, never to the working
            # directory the launcher happened to be started from.
            path = os.path.normpath(os.path.join(home, os.path.expanduser(folder)))
            if path not in roots:
                roots.append(path)
        return cls(
            roots=tuple(roots),
            exclude=files_config.exclude,
            max_depth=files_config.max_depth,
            show_hidden=files_config.show_hidden,
        )

    def skip(self, name: str) -> bool:
        if not self.show_hidden and name.startswith("."):
            return True
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.exclude)


@dataclass(frozen=True)
class FileEntry:
    path: str
    name: str
    is_dir: bool
    mtime: float
    root: str
    depth: int  # 1 = directly inside a root

    @property
    def parent(self) -> str:
        return os.path.dirname(self.path)


@dataclass
class IndexState:
    entries: dict[str, FileEntry] = field(default_factory=dict)
    children: dict[str, set[str]] = field(default_factory=dict)  # dir -> child paths
    truncated: bool = False

    @property
    def directories(self) -> list[str]:
        """Directories to watch: the roots and every indexed folder."""
        return list(self.children)


def scan(settings: IndexSettings, max_entries: int = MAX_ENTRIES) -> IndexState:
    state = IndexState()
    for root in settings.roots:
        if not os.path.isdir(root):
            log.warning("file search folder %s does not exist", root)
            continue
        _scan_tree(state, settings, root, root, 0, max_entries)
    log.info(
        "indexed %d files and folders under %s%s",
        len(state.entries),
        ", ".join(settings.roots),
        " (stopped at the limit)" if state.truncated else "",
    )
    return state


def _scan_tree(
    state: IndexState, settings: IndexSettings, root: str, directory: str, depth: int, limit: int
) -> list[str]:
    """Index `directory` (at `depth`) and everything below it. Returns the dirs indexed."""
    added_dirs = [directory]
    pending = [(directory, depth)]
    while pending:
        current, level = pending.pop()
        children = state.children.setdefault(current, set())
        for entry in _list_dir(current, settings):
            if len(state.entries) >= limit:
                state.truncated = True
                return added_dirs
            state.entries[entry.path] = FileEntry(
                entry.path, entry.name, entry.is_dir, entry.mtime, root, level + 1
            )
            children.add(entry.path)
            if entry.is_dir and level + 1 < settings.max_depth:
                pending.append((entry.path, level + 1))
                added_dirs.append(entry.path)
    return added_dirs


@dataclass(frozen=True)
class _Listing:
    path: str
    name: str
    is_dir: bool
    mtime: float


def _list_dir(directory: str, settings: IndexSettings) -> list[_Listing]:
    try:
        with os.scandir(directory) as it:
            items = []
            for dirent in it:
                if settings.skip(dirent.name):
                    continue
                try:
                    # Never follow symlinks: no loops, and no escaping the chosen folders.
                    is_dir = dirent.is_dir(follow_symlinks=False)
                    mtime = dirent.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue  # vanished while listing
                items.append(_Listing(dirent.path, dirent.name, is_dir, mtime))
            return items
    except OSError as e:
        log.debug("cannot list %s: %s", directory, e)
        return []


class FileIndex:
    def __init__(self, settings: IndexSettings | None = None) -> None:
        self.settings = settings or IndexSettings(roots=())
        self._state = IndexState()
        # Search acceleration: all folded names joined by "\n", with line start offsets.
        self._blob = ""
        self._blob_starts: list[int] = []
        self._blob_entries: list[FileEntry] = []
        self._dirty = True

    # --- state management (main thread) -------------------------------------------

    def install(self, settings: IndexSettings, state: IndexState) -> None:
        self.settings = settings
        self._state = state
        self._dirty = True

    @property
    def directories(self) -> list[str]:
        return self._state.directories

    def __len__(self) -> int:
        return len(self._state.entries)

    def rescan_dir(self, directory: str) -> tuple[list[str], list[str]]:
        """Re-read one directory. Returns (directories added, directories removed)."""
        if directory not in self._state.children:
            return [], []
        root, depth = self._root_and_depth(directory)
        if root is None:
            return [], []
        known = self._state.children[directory]
        current = {item.path: item for item in _list_dir(directory, self.settings)}
        added_dirs: list[str] = []
        removed_dirs: list[str] = []

        for path in known - current.keys():
            removed_dirs += self._remove_tree(path)
            known.discard(path)
        for path, item in current.items():
            old = self._state.entries.get(path)
            if old is not None and old.is_dir == item.is_dir:
                if old.mtime != item.mtime:
                    self._state.entries[path] = FileEntry(
                        path, item.name, item.is_dir, item.mtime, root, depth + 1
                    )
                    self._dirty = True
                continue
            if old is not None:  # file replaced by a folder or vice versa
                removed_dirs += self._remove_tree(path)
            if len(self._state.entries) >= MAX_ENTRIES:
                self._state.truncated = True
                break
            self._state.entries[path] = FileEntry(
                path, item.name, item.is_dir, item.mtime, root, depth + 1
            )
            known.add(path)
            if item.is_dir and depth + 1 < self.settings.max_depth:
                added_dirs += _scan_tree(
                    self._state, self.settings, root, path, depth + 1, MAX_ENTRIES
                )
        self._dirty = True
        return added_dirs, removed_dirs

    def _root_and_depth(self, directory: str) -> tuple[str | None, int]:
        if directory in self.settings.roots:
            return directory, 0
        entry = self._state.entries.get(directory)
        return (entry.root, entry.depth) if entry else (None, 0)

    def _remove_tree(self, path: str) -> list[str]:
        removed_dirs = []
        stack = [path]
        while stack:
            current = stack.pop()
            self._state.entries.pop(current, None)
            children = self._state.children.pop(current, None)
            if children is not None:
                removed_dirs.append(current)
                stack.extend(children)
        self._dirty = True
        return removed_dirs

    # --- queries ----------------------------------------------------------------------

    def search(
        self, query: str, limit: int, now: float | None = None
    ) -> list[tuple[FileEntry, float]]:
        now = time.time() if now is None else now
        q = query.strip().casefold()
        if not q:
            return self.recent(limit, now)
        by_path = "/" in q  # "docs/report" matches against the path below the folder
        scored = []
        for entry in self._candidates(q, by_path):
            target = os.path.relpath(entry.path, entry.root) if by_path else entry.name
            score = fuzzy_score(q, target)
            if score is not None:
                scored.append((entry, score * (1 + RECENCY_WEIGHT * _recency(entry.mtime, now))))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    def recent(self, limit: int, now: float | None = None) -> list[tuple[FileEntry, float]]:
        """Most recently modified files first (folders excluded)."""
        now = time.time() if now is None else now
        files = [e for e in self._state.entries.values() if not e.is_dir]
        files.sort(key=lambda e: e.mtime, reverse=True)
        return [(e, _recency(e.mtime, now)) for e in files[:limit]]

    def _candidates(self, q: str, by_path: bool) -> Iterable[FileEntry]:
        if by_path:
            return self._state.entries.values()  # rare; the plain loop is fine
        self._rebuild_blob()
        # Regex over one big string runs in C: only names containing the query's
        # characters in order survive, then Python scores just those.
        chars = [re.escape(ch) for ch in q if not ch.isspace()]
        pattern = re.compile("[^\n]*?".join(chars))
        seen = set()
        out = []
        for match in pattern.finditer(self._blob):
            line = bisect.bisect_right(self._blob_starts, match.start()) - 1
            if line not in seen:
                seen.add(line)
                out.append(self._blob_entries[line])
        return out

    def _rebuild_blob(self) -> None:
        if not self._dirty:
            return
        self._blob_entries = list(self._state.entries.values())
        names = [e.name.casefold().replace("\n", " ") for e in self._blob_entries]
        self._blob_starts = []
        offset = 0
        for name in names:
            self._blob_starts.append(offset)
            offset += len(name) + 1
        self._blob = "\n".join(names)
        self._dirty = False


def _recency(mtime: float, now: float) -> float:
    age_days = max(now - mtime, 0.0) / 86400.0
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def display_dir(path: str, home: str | None = None) -> str:
    """/home/me/Downloads/sub -> ~/Downloads/sub"""
    home = home or str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


def ago(mtime: float, now: float | None = None) -> str:
    seconds = max((time.time() if now is None else now) - mtime, 0)
    for unit, size in (("y", 365 * 86400), ("mo", 30 * 86400), ("d", 86400), ("h", 3600)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    minutes = int(seconds // 60)
    return f"{minutes}m ago" if minutes else "just now"
