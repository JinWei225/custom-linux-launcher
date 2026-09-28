"""Notes (from the Notes window's folder) in the main search, by title.

Enter opens the note in Notes; Alt+Enter shows its file in the file manager. The
folder is rescanned at most once a second while typing, and only notes whose files
changed are read again.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from ..config import Config
from ..files_index import ago
from ..notes_store import Note, NotesStore
from ..ranking import best_score
from .base import Host, Result

ICON = "accessories-text-editor"  # the Notes app's icon
MAX_AGE = 1.0  # seconds a scan of the notes folder is reused for


class NotesProvider:
    name = "notes"

    def __init__(self, host: Host, clock: Callable[[], float] = time.monotonic) -> None:
        self._host = host
        self._clock = clock
        self._store: NotesStore | None = None
        self._notes: list[Note] = []
        self._scanned: float | None = None

    def configure(self, config: Config) -> None:
        root = Path(os.path.expanduser(config.notes.folder))
        if self._store is None or self._store.root != root:
            self._store = NotesStore(root)
            self._scanned = None

    def query(self, text: str) -> list[Result]:
        query = text.strip()
        if not query or self._store is None:
            return []
        results = []
        for note in self._all():
            score = best_score(query, (note.title, Path(note.rel).stem))
            if score is not None:
                results.append(self._result(note, score))
        return results

    def _all(self) -> list[Note]:
        now = self._clock()
        if self._scanned is None or now - self._scanned >= MAX_AGE:
            self._notes = self._store.all_notes() if self._store.root.is_dir() else []
            self._scanned = now
        return self._notes

    def _result(self, note: Note, score: float) -> Result:
        path = str(self._store.root / note.rel)
        where = f"Notes/{note.folder}" if note.folder else "Notes"
        return Result(
            id=f"note:{note.rel}",
            title=note.title,
            subtitle=f"{where} · {ago(note.mtime)}",
            icon=ICON,
            action=lambda: self._host.open_note(path),
            alt_action=lambda: self._host.reveal_file(path),
            score=score,
        )
