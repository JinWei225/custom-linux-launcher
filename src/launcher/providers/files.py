"""File search over the configured folders (the index lives in files_index.py)."""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import lru_cache

from ..files_index import FileEntry, FileIndex, ago, display_dir
from .base import Host, Result

# Oversample from the index so the engine's own limit still has good results to pick from.
INDEX_LIMIT = 50


@lru_cache(maxsize=512)
def icon_for_name(name: str) -> str:
    """Themed icon for a file name, by its guessed content type (cached per name)."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        content_type, _uncertain = Gio.content_type_guess(name, None)
        icon = Gio.content_type_get_icon(content_type)
        return icon.to_string() if icon else "text-x-generic"
    except Exception:
        return "text-x-generic"


def _icon(entry: FileEntry) -> str:
    return "folder" if entry.is_dir else icon_for_name(_extension_key(entry.name))


def _extension_key(name: str) -> str:
    # Cache by extension, not full name: "a.pdf" and "b.pdf" share one lookup.
    _, ext = os.path.splitext(name)
    return f"file{ext.lower()}" if ext else name


class FilesProvider:
    name = "files"

    def __init__(
        self,
        host: Host,
        index: FileIndex,
        icon: Callable[[FileEntry], str] = _icon,
    ) -> None:
        self._host = host
        self._index = index
        self._icon = icon

    def query(self, text: str) -> list[Result]:
        return [
            self._result(entry, score) for entry, score in self._index.search(text, INDEX_LIMIT)
        ]

    def _result(self, entry: FileEntry, score: float) -> Result:
        where = display_dir(entry.parent)
        subtitle = f"{where} · {ago(entry.mtime)}" if not entry.is_dir else where
        return Result(
            id=f"file:{entry.path}",
            title=entry.name,
            subtitle=subtitle,
            icon=self._icon(entry),
            action=lambda: self._host.open_file(entry.path),
            alt_action=lambda: self._host.reveal_file(entry.path),
            score=score,
        )
