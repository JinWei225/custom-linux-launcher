"""Turns the helper's ClipboardChanged signals into clipboard history entries."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

from . import APP_ID  # noqa: E402
from .clipboard_store import ClipboardStore, app_matches, choose_mimetype, is_secret  # noqa: E402
from .config import ClipboardConfig  # noqa: E402
from .helper import Helper  # noqa: E402

log = logging.getLogger(__name__)

ICON_SIZE = 64
PREVIEW_SIZE = 480
SKIP_SECONDS = 5


def make_thumbnails(data: bytes) -> tuple[int, int, bytes, bytes]:
    """(width, height, icon png, preview png). Safe to call from a worker thread."""
    loader = GdkPixbuf.PixbufLoader()
    loader.write(data)
    loader.close()
    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        raise ValueError("not a readable image")
    pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    width, height = pixbuf.get_width(), pixbuf.get_height()

    def scaled(size: int) -> bytes:
        factor = min(size / width, size / height, 1.0)
        small = pixbuf.scale_simple(
            max(1, round(width * factor)),
            max(1, round(height * factor)),
            GdkPixbuf.InterpType.BILINEAR,
        )
        ok, png = small.save_to_bufferv("png", [], [])
        if not ok:
            raise ValueError("could not encode thumbnail")
        return bytes(png)

    return width, height, scaled(ICON_SIZE), scaled(PREVIEW_SIZE)


class ClipboardRecorder:
    def __init__(
        self,
        helper: Helper,
        store: ClipboardStore,
        config: Callable[[], ClipboardConfig],
        on_added: Callable[[], None],
    ) -> None:
        self._helper = helper
        self._store = store
        self._config = config
        self._on_added = on_added
        self.paused = False
        # The history entry the clipboard holds right now, if known. A snippet paste
        # puts this back afterwards; unknown content (paused, excluded, a password) is
        # never restored.
        self.current: int | None = None
        self._skip: dict[str, float] = {}  # text -> monotonic deadline
        helper.subscribe_clipboard(self._on_changed)

    def skip_text(self, text: str) -> None:
        """Don't record this text if it is copied in the next few seconds (a snippet
        being pasted)."""
        self._skip[text] = time.monotonic() + SKIP_SECONDS

    def _on_changed(self, mimetypes: list[str], wm_class: str, app_id: str) -> None:
        self.current = None
        config = self._config()
        log.debug("clipboard changed: %s from %s / %s", mimetypes, wm_class, app_id)
        source = app_id.removesuffix(".desktop") or wm_class
        if app_matches((APP_ID,), wm_class, app_id):
            source = ""  # we put it there (pasting an entry): keep the entry's own source
        if not config.enabled or self.paused:
            return
        if is_secret(mimetypes):
            log.info("not recording: %s marked the copy as a password", source or "an app")
            return
        if app_matches(config.exclude_apps, wm_class, app_id):
            log.info("not recording: %s is in [clipboard] exclude_apps", source)
            return
        choice = choose_mimetype(mimetypes)
        if choice is None:
            return  # nothing we keep (e.g. only file-manager internals)
        kind, mime = choice
        self._helper.get_clipboard(
            mime, lambda data: self._received(kind, mime, data, source, config)
        )

    def _received(
        self, kind: str, mime: str, data: bytes | None, source: str, config: ClipboardConfig
    ) -> None:
        if not data:
            log.debug("clipboard %s came back empty", mime)
            return
        if kind == "text":
            text = data.decode("utf-8", errors="replace")
            if self._skip.pop(text, 0) > time.monotonic():
                return
            clip = self._store.add_text(text, source)
            if clip is not None:
                self.current = clip.id
                self._after_add(config)
            return
        if len(data) > config.max_image_mb * 1024 * 1024:
            log.info(
                "image of %.1f MB not recorded (limit %d MB)", len(data) / 1e6, config.max_image_mb
            )
            return

        def work() -> None:
            try:
                thumbs = make_thumbnails(data)
            except (GLib.Error, ValueError) as e:
                log.warning("copied %s could not be decoded: %s", mime, e)
                return
            GLib.idle_add(self._store_image, data, mime, source, thumbs, config)

        threading.Thread(target=work, name="clipboard-thumbnail", daemon=True).start()

    def _store_image(self, data, mime, source, thumbs, config) -> bool:
        width, height, icon, preview = thumbs
        self.current = self._store.add_image(data, mime, source, width, height, icon, preview).id
        self._after_add(config)
        return GLib.SOURCE_REMOVE

    def _after_add(self, config: ClipboardConfig) -> None:
        removed = self._store.prune(config.max_entries, config.max_days)
        if removed:
            log.debug("pruned %d old clipboard entries", removed)
        self._on_added()
