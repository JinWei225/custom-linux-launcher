"""Collects results from the providers enabled in a mode and ranks them."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from .providers.base import Provider, Result

log = logging.getLogger(__name__)

# Providers queried in each mode. Later milestones fill these in.
MODES: dict[str, tuple[str, ...]] = {
    "all": ("commands",),
    "apps": (),
    "files": (),
    "clipboard": (),
    "snippets": (),
}


class Engine:
    def __init__(self, providers: Mapping[str, Provider]) -> None:
        self._providers = dict(providers)

    def query(self, text: str, mode: str, limit: int) -> list[Result]:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        results: list[Result] = []
        for name in MODES[mode]:
            provider = self._providers.get(name)
            if provider is None:
                continue
            try:
                results.extend(provider.query(text))
            except Exception:
                # One broken provider must never take the whole launcher down.
                log.exception("provider %r failed for query %r", name, text)
        results.sort(key=lambda r: r.score, reverse=True)  # stable: keeps provider order on ties
        return results[:limit]
