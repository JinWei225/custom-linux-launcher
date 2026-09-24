"""Clipboard history storage: SQLite for metadata and text, files for images.

Pure Python (no GTK) so it can be tested; image decoding and thumbnails are done by
the caller (clipboard_recorder.py) and handed in as bytes.

Layout under the data directory (private: 0700 / 0600):
    clipboard.db
    clips/<digest>.data          original image bytes (pasted back as-is)
    clips/<digest>.icon.png      64 px thumbnail for the result list
    clips/<digest>.preview.png   480 px image for the preview pane
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .ranking import fuzzy_score

log = logging.getLogger(__name__)

MAX_TEXT_BYTES = 1024 * 1024
# Password managers mark secrets with these content types (KeePassXC, KDE).
SECRET_HINTS = ("x-kde-passwordManagerHint", "application/x-nspasteboard-concealed-type")
TEXT_TYPES = ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING", "TEXT")
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff")


def choose_mimetype(mimetypes: list[str]) -> tuple[str, str] | None:
    """What to record from an offer: ("text", mime) or ("image", mime), or None.

    Text wins when both are offered: spreadsheets and office apps put a picture of the
    selection next to the text, and the text is what you want back.
    """
    offered = set(mimetypes)
    for mime in TEXT_TYPES:
        if mime in offered:
            return "text", mime
    for mime in IMAGE_TYPES:
        if mime in offered:
            return "image", mime
    other_image = next((m for m in mimetypes if m.startswith("image/")), None)
    return ("image", other_image) if other_image else None


def is_secret(mimetypes: list[str]) -> bool:
    return any(hint in mimetypes for hint in SECRET_HINTS)


def app_matches(patterns: tuple[str, ...], wm_class: str, app_id: str) -> bool:
    """Case-insensitive match of an app against ids / window classes ("x.desktop" ok)."""
    names = {n.casefold().removesuffix(".desktop") for n in (wm_class, app_id) if n}
    return any(p.casefold().removesuffix(".desktop") in names for p in patterns)


@dataclass(frozen=True)
class Clip:
    id: int
    kind: str  # "text" | "image"
    text: str  # the text, or "" for images
    mime: str
    size: int  # bytes of the original content
    source: str  # app it was copied from (best effort)
    created: float  # last time it was copied (re-copying moves it to the top)
    pinned: bool
    width: int = 0
    height: int = 0
    digest: str = ""


class ClipboardStore:
    def __init__(self, directory: Path | None) -> None:
        """directory=None keeps everything in memory (tests)."""
        self._dir = directory
        if directory is not None:
            (directory / "clips").mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            os.chmod(directory / "clips", 0o700)
            path = directory / "clipboard.db"
            self._db = sqlite3.connect(path)
            os.chmod(path, 0o600)
            self._db.execute("PRAGMA journal_mode=WAL")
        else:
            self._db = sqlite3.connect(":memory:")
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS clips ("
            " id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, mime TEXT NOT NULL,"
            " size INTEGER NOT NULL, source TEXT NOT NULL, created REAL NOT NULL,"
            " pinned INTEGER NOT NULL DEFAULT 0, width INTEGER NOT NULL DEFAULT 0,"
            " height INTEGER NOT NULL DEFAULT 0, digest TEXT NOT NULL UNIQUE)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS clips_created ON clips (created)")
        self._db.commit()
        self._blobs: dict[str, bytes] = {}  # in-memory mode only

    # --- adding --------------------------------------------------------------------------

    def add_text(self, text: str, source: str, now: float | None = None) -> Clip | None:
        if not text.strip():
            return None
        data = text.encode("utf-8")
        if len(data) > MAX_TEXT_BYTES:
            log.info(
                "clipboard text of %d bytes not recorded (limit %d)", len(data), MAX_TEXT_BYTES
            )
            return None
        digest = hashlib.sha256(b"text\0" + data).hexdigest()
        return self._upsert(
            "text", text, "text/plain;charset=utf-8", len(data), source, digest, now
        )

    def add_image(
        self,
        data: bytes,
        mime: str,
        source: str,
        width: int,
        height: int,
        icon_png: bytes,
        preview_png: bytes,
        now: float | None = None,
    ) -> Clip:
        digest = hashlib.sha256(b"image\0" + data).hexdigest()
        existing = self._by_digest(digest)
        if existing is None:
            self._write(f"{digest}.data", data)
            self._write(f"{digest}.icon.png", icon_png)
            self._write(f"{digest}.preview.png", preview_png)
        return self._upsert("image", "", mime, len(data), source, digest, now, width, height)

    def _upsert(self, kind, text, mime, size, source, digest, now, width=0, height=0) -> Clip:
        now = time.time() if now is None else now
        existing = self._by_digest(digest)
        if existing is not None:
            # Copying the same thing again just moves it to the top.
            # Keep the original source when the launcher itself re-copies an entry.
            self._db.execute(
                "UPDATE clips SET created = ?, source = COALESCE(NULLIF(?, ''), source)"
                " WHERE id = ?",
                (now, source, existing.id),
            )
        else:
            self._db.execute(
                "INSERT INTO clips (kind, text, mime, size, source, created, width, height, digest)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, text, mime, size, source, now, width, height, digest),
            )
        self._db.commit()
        return self._by_digest(digest)

    # --- reading -------------------------------------------------------------------------

    def recent(self, limit: int) -> list[Clip]:
        rows = self._db.execute("SELECT * FROM clips ORDER BY created DESC LIMIT ?", (limit,))
        return [_clip(r) for r in rows]

    def search(self, query: str, limit: int) -> list[tuple[Clip, float]]:
        """Clips matching the query, best first (ties: most recent first)."""
        q = query.strip().casefold()
        if not q:
            return [(c, 0.0) for c in self.recent(limit)]
        scored = []
        for clip in self.recent(10_000):
            score = _match(q, clip)
            if score is not None:
                scored.append((clip, score))
        scored.sort(key=lambda pair: (pair[1], pair[0].created), reverse=True)
        return scored[:limit]

    def get(self, clip_id: int) -> Clip | None:
        row = self._db.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return _clip(row) if row else None

    def content(self, clip_id: int) -> tuple[str, bytes] | None:
        """(mime, bytes) to put back on the clipboard."""
        clip = self.get(clip_id)
        if clip is None:
            return None
        if clip.kind == "text":
            return clip.mime, clip.text.encode("utf-8")
        data = self._read(f"{clip.digest}.data")
        return (clip.mime, data) if data is not None else None

    def image_path(self, clip: Clip, variant: str) -> str | None:
        """Path of "icon" or "preview" for an image clip (None in memory mode)."""
        if self._dir is None or clip.kind != "image":
            return None
        return str(self._dir / "clips" / f"{clip.digest}.{variant}.png")

    def __len__(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM clips").fetchone()[0]

    # --- changing ------------------------------------------------------------------------

    def set_pinned(self, clip_id: int, pinned: bool) -> None:
        self._db.execute("UPDATE clips SET pinned = ? WHERE id = ?", (int(pinned), clip_id))
        self._db.commit()

    def delete(self, clip_id: int) -> None:
        clip = self.get(clip_id)
        if clip is None:
            return
        self._db.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        self._db.commit()
        self._remove_files(clip)

    def clear(self, keep_pinned: bool = True, since: float | None = None) -> int:
        """Delete entries (copied at or after `since`, if given). Returns how many."""
        conditions, params = [], []
        if keep_pinned:
            conditions.append("pinned = 0")
        if since is not None:
            conditions.append("created >= ?")
            params.append(since)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        doomed = [_clip(r) for r in self._db.execute("SELECT * FROM clips" + where, params)]
        self._delete_many(doomed)
        return len(doomed)

    def counts(self) -> tuple[int, int]:
        """(entries, pinned entries)."""
        row = self._db.execute("SELECT COUNT(*), COALESCE(SUM(pinned), 0) FROM clips").fetchone()
        return row[0], row[1]

    def prune(self, max_entries: int, max_days: int, now: float | None = None) -> int:
        """Drop unpinned clips beyond the count or older than max_days."""
        now = time.time() if now is None else now
        unpinned = [
            _clip(r)
            for r in self._db.execute("SELECT * FROM clips WHERE pinned = 0 ORDER BY created DESC")
        ]
        cutoff = now - max_days * 86400
        doomed = [c for i, c in enumerate(unpinned) if i >= max_entries or c.created < cutoff]
        self._delete_many(doomed)
        return len(doomed)

    def close(self) -> None:
        self._db.close()

    # --- internals -----------------------------------------------------------------------

    def _delete_many(self, clips: list[Clip]) -> None:
        if not clips:
            return
        self._db.executemany("DELETE FROM clips WHERE id = ?", [(c.id,) for c in clips])
        self._db.commit()
        for clip in clips:
            self._remove_files(clip)

    def _by_digest(self, digest: str) -> Clip | None:
        row = self._db.execute("SELECT * FROM clips WHERE digest = ?", (digest,)).fetchone()
        return _clip(row) if row else None

    def _write(self, name: str, data: bytes) -> None:
        if self._dir is None:
            self._blobs[name] = data
            return
        path = self._dir / "clips" / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)

    def _read(self, name: str) -> bytes | None:
        if self._dir is None:
            return self._blobs.get(name)
        try:
            return (self._dir / "clips" / name).read_bytes()
        except OSError:
            return None

    def _remove_files(self, clip: Clip) -> None:
        if clip.kind != "image":
            return
        for suffix in ("data", "icon.png", "preview.png"):
            name = f"{clip.digest}.{suffix}"
            if self._dir is None:
                self._blobs.pop(name, None)
            else:
                (self._dir / "clips" / name).unlink(missing_ok=True)


def _clip(row: sqlite3.Row) -> Clip:
    return Clip(
        id=row["id"],
        kind=row["kind"],
        text=row["text"],
        mime=row["mime"],
        size=row["size"],
        source=row["source"],
        created=row["created"],
        pinned=bool(row["pinned"]),
        width=row["width"],
        height=row["height"],
        digest=row["digest"],
    )


def _match(q: str, clip: Clip) -> float | None:
    if clip.kind == "image":
        label = f"image {clip.mime.removeprefix('image/')} {clip.source}"
        return fuzzy_score(q, label)
    head = clip.text[:5000].casefold()
    title = first_line(clip.text).casefold()
    if q in head:
        # Prefer matches you can see in the list (the first line), then at the start.
        return 0.7 + (0.05 if q in title else 0.0) + (0.1 if head.startswith(q) else 0.0)
    score = fuzzy_score(q, title)
    return score * 0.8 if score is not None else None


def first_line(text: str, width: int = 200) -> str:
    """The first non-blank line, whitespace collapsed, for list titles."""
    line = next((line for line in text.splitlines() if line.strip()), "")
    line = " ".join(line.split())
    return line if len(line) <= width else line[: width - 1] + "…"


def read_counts(directory: Path) -> tuple[int, int] | None:
    """(entries, pinned) without writing anything; for Launcher Settings (another process)."""
    path = directory / "clipboard.db"
    if not path.exists():
        return (0, 0)
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = db.execute("SELECT COUNT(*), COALESCE(SUM(pinned), 0) FROM clips").fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return row[0], row[1]
