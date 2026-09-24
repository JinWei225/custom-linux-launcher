"""Clipboard history results (the store lives in clipboard_store.py)."""

from __future__ import annotations

from collections.abc import Callable

from ..clipboard_store import Clip, ClipboardStore, first_line
from ..files_index import ago
from .base import Host, Result

# The list scrolls in clipboard mode, so show far more than the usual result count.
LIMIT = 200
PREVIEW_CHARS = 20_000


def human_size(size: int) -> str:
    for unit, factor in (("MB", 1024 * 1024), ("KB", 1024)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}"
    return f"{size} B"


class ClipboardProvider:
    name = "clipboard"

    def __init__(
        self,
        host: Host,
        store: ClipboardStore,
        app_name: Callable[[str], str] = lambda source: source,
    ) -> None:
        self._host = host
        self._store = store
        self._app_name = app_name  # "brave_brave" -> "Brave Web Browser"

    def query(self, text: str) -> list[Result]:
        matches = [clip for clip, _score in self._store.search(text, LIMIT)]
        # Pinned entries form their own section at the top; each section keeps the
        # store's order (best match, then most recent). Scores only need to sort.
        pinned = [c for c in matches if c.pinned]
        ordered = pinned + [c for c in matches if not c.pinned]
        sections = bool(pinned)
        return [
            self._result(
                clip, 1.0 - i * 1e-4, ("Pinned" if clip.pinned else "Recent") if sections else None
            )
            for i, clip in enumerate(ordered)
        ]

    def _result(self, clip: Clip, score: float, section: str | None) -> Result:
        parts = []
        if clip.source:
            parts.append(self._app_name(clip.source))
        parts.append(ago(clip.created))
        if clip.kind == "image":
            title = f"Image {clip.width}×{clip.height}"
            parts.append(human_size(clip.size))
            icon = self._store.image_path(clip, "icon") or "image-x-generic"
            preview_path = self._store.image_path(clip, "preview")
            preview = ("image", preview_path) if preview_path else None
        else:
            title = first_line(clip.text)
            chars = len(clip.text)
            parts.append(f"{chars:,} character{'s' if chars != 1 else ''}")
            icon = "text-x-generic"
            shown = clip.text[:PREVIEW_CHARS]
            if len(clip.text) > PREVIEW_CHARS:
                shown += "\n\n… (preview truncated)"
            preview = ("text", shown)
        return Result(
            id=f"clip:{clip.id}",
            title=title,
            subtitle=" · ".join(parts),
            icon=icon,
            action=lambda: self._host.paste_clip(clip.id),
            alt_action=lambda: self._host.copy_clip(clip.id),
            preview=preview,
            key_actions={
                "pin": lambda: self._host.pin_clip(clip.id, not clip.pinned),
                "delete": lambda: self._host.delete_clip(clip.id),
            },
            learn=False,  # the list is ordered by recency, not by how often you pick things
            score=score,
            section=section,
        )
