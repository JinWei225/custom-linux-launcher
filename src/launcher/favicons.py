"""Website icons for quicklinks, fetched in the background and cached on disk.

Icons come from Google's favicon service (the domain of each quicklink is sent to
Google once per REFRESH_AFTER). Lookups never block: a missing icon returns None and
starts a background fetch; `on_update` is called (on the main loop, via
`call_soon`) when a new icon lands so the UI can refresh.

Pure Python (no GTK) so it can be tested with a fake fetcher.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlsplit

log = logging.getLogger(__name__)

SERVICE_URL = "https://www.google.com/s2/favicons?domain={domain}&sz=64"
REFRESH_AFTER = 30 * 86400  # re-download cached icons after 30 days
RETRY_MISSING_AFTER = 3 * 86400  # a site had no icon: ask again after 3 days
RETRY_ERROR_AFTER = 10 * 60  # network error (offline?): retry after 10 minutes
TIMEOUT_SECONDS = 8
MAX_BYTES = 512 * 1024

_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",  # JPEG
    b"GIF8",
    b"\x00\x00\x01\x00",  # ICO
    b"RIFF",  # WebP
    b"<svg",
    b"<?xml",
)


class FetchError(Exception):
    """Temporary failure (network down, server error): try again later."""


def looks_like_image(data: bytes) -> bool:
    return any(data.startswith(magic) for magic in _IMAGE_MAGIC)


def domain_of(url: str) -> str | None:
    host = urlsplit(url).hostname
    return host.lower() if host else None


def candidate_domains(domain: str) -> list[str]:
    """web.whatsapp.com -> [web.whatsapp.com, whatsapp.com]; stops at two labels."""
    labels = domain.split(".")
    return [".".join(labels[i:]) for i in range(max(len(labels) - 1, 1))]


def fetch_favicon(domain: str) -> bytes | None:
    """Icon bytes, None if the site has no icon, FetchError on temporary failures."""
    for candidate in candidate_domains(domain):
        url = SERVICE_URL.format(domain=quote(candidate, safe=""))
        request = urllib.request.Request(url, headers={"User-Agent": "launcher-favicons/1"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                data = response.read(MAX_BYTES)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue  # the service has no icon for this name; try the parent domain
            raise FetchError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError(str(e)) from e
        if looks_like_image(data):
            return data
    return None


class FaviconCache:
    def __init__(
        self,
        cache_dir: Path,
        fetch: Callable[[str], bytes | None] = fetch_favicon,
        on_update: Callable[[], None] | None = None,
        call_soon: Callable[[Callable[[], None]], None] = lambda fn: fn(),
        executor: ThreadPoolExecutor | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._dir = cache_dir
        self._fetch = fetch
        self._on_update = on_update
        self._call_soon = call_soon
        self._executor = executor or ThreadPoolExecutor(max_workers=4, thread_name_prefix="favicon")
        self._now = now
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()
        self._errors: dict[str, float] = {}  # domain -> time of last temporary failure

    def icon_for(self, url: str) -> str | None:
        """Path of the cached icon for this URL's site, or None (a fetch may start)."""
        domain = domain_of(url)
        if domain is None:
            return None
        icon = self._icon_path(domain)
        try:
            age = self._now() - icon.stat().st_mtime
        except FileNotFoundError:
            self._maybe_fetch(domain)
            return None
        if age > REFRESH_AFTER:
            self._maybe_fetch(domain)  # keep showing the old icon meanwhile
        return str(icon)

    def prefetch(self, urls: list[str]) -> None:
        for url in urls:
            self.icon_for(url)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _icon_path(self, domain: str) -> Path:
        return self._dir / f"{domain}.icon"

    def _missing_marker(self, domain: str) -> Path:
        return self._dir / f"{domain}.missing"

    def _maybe_fetch(self, domain: str) -> None:
        now = self._now()
        if now - self._errors.get(domain, -RETRY_ERROR_AFTER) < RETRY_ERROR_AFTER:
            return
        try:
            if now - self._missing_marker(domain).stat().st_mtime < RETRY_MISSING_AFTER:
                return
        except FileNotFoundError:
            pass
        with self._lock:
            if domain in self._in_flight:
                return
            self._in_flight.add(domain)
        self._executor.submit(self._fetch_and_store, domain)

    def _fetch_and_store(self, domain: str) -> None:
        try:
            data = self._fetch(domain)
        except FetchError as e:
            log.info("favicon for %s not available yet: %s", domain, e)
            self._errors[domain] = self._now()
            return
        except Exception:
            log.exception("favicon fetch for %s failed", domain)
            self._errors[domain] = self._now()
            return
        finally:
            with self._lock:
                self._in_flight.discard(domain)

        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            if data is None:
                self._missing_marker(domain).touch()
                log.info("no favicon for %s", domain)
                return
            tmp = self._icon_path(domain).with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, self._icon_path(domain))  # atomic: never a half-written icon
            self._missing_marker(domain).unlink(missing_ok=True)
        except OSError as e:
            log.error("cannot store favicon for %s: %s", domain, e)
            return
        log.debug("stored favicon for %s", domain)
        if self._on_update is not None:
            self._call_soon(self._on_update)
