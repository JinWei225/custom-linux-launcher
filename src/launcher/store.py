"""Usage history for frecency ranking (how often + how recently a result was picked).

Each result id keeps one exponentially decaying counter: every pick adds 1, and the
value halves every HALF_LIFE_DAYS. This needs no per-pick history and naturally
forgets things you stopped using.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

HALF_LIFE_DAYS = 14.0
# boost = value / (value + SATURATION): 1 pick -> 0.25, 3 -> 0.5, 9 -> 0.75, never 1.
SATURATION = 3.0
_SECONDS_PER_DAY = 86400.0


def decay(value: float, elapsed_seconds: float) -> float:
    return value * 0.5 ** (max(elapsed_seconds, 0.0) / (HALF_LIFE_DAYS * _SECONDS_PER_DAY))


class UsageStore:
    """Frecency data, cached in memory and written through to SQLite."""

    def __init__(self, path: Path | None) -> None:
        # path=None keeps everything in memory (tests, or when the db cannot be opened).
        self._db = self._open(path)
        self._usage: dict[str, tuple[float, float]] = {
            row[0]: (row[1], row[2])
            for row in self._db.execute("SELECT id, value, updated FROM usage")
        }

    @staticmethod
    def _open(path: Path | None) -> sqlite3.Connection:
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(path)
                db.execute("PRAGMA journal_mode=WAL")
                return UsageStore._init_schema(db)
            except (OSError, sqlite3.Error) as e:
                log.error("cannot open usage database %s (%s); history will not be saved", path, e)
        return UsageStore._init_schema(sqlite3.connect(":memory:"))

    @staticmethod
    def _init_schema(db: sqlite3.Connection) -> sqlite3.Connection:
        db.execute(
            "CREATE TABLE IF NOT EXISTS usage ("
            " id TEXT PRIMARY KEY, value REAL NOT NULL, updated REAL NOT NULL)"
        )
        db.commit()
        return db

    def boost(self, result_id: str, now: float | None = None) -> float:
        """0 for never used, approaching 1 for frequently and recently used."""
        entry = self._usage.get(result_id)
        if entry is None:
            return 0.0
        value = decay(entry[0], (time.time() if now is None else now) - entry[1])
        return value / (value + SATURATION)

    def record(self, result_id: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        old = self._usage.get(result_id)
        value = (decay(old[0], now - old[1]) if old else 0.0) + 1.0
        self._usage[result_id] = (value, now)
        try:
            self._db.execute(
                "INSERT INTO usage (id, value, updated) VALUES (?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET value = excluded.value, updated = excluded.updated",
                (result_id, value, now),
            )
            self._db.commit()
        except sqlite3.Error as e:
            log.error("cannot save usage for %s: %s", result_id, e)

    def close(self) -> None:
        self._db.close()
