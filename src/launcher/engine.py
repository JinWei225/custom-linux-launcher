"""Collects results from the providers enabled in a mode and ranks them."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace

from .config import Config
from .providers.base import Provider, Result
from .store import UsageStore

log = logging.getLogger(__name__)

# Providers queried in each mode, in tie-break order.
MODES: dict[str, tuple[str, ...]] = {
    "all": ("apps", "quicklinks", "snippet-search", "commands", "websearch"),
    "apps": ("apps",),
    "files": ("files",),
    "clipboard": ("clipboard",),
    "snippets": ("snippets",),
}

# final score = match score * (1 + FRECENCY_WEIGHT * boost), boost in [0, 1).
# 0.7 lets a frequently used app with a prefix match (0.8-0.9) overtake an unused exact
# match (1.0), but never an exact alias (2.0).
FRECENCY_WEIGHT = 0.7


class Engine:
    def __init__(self, providers: Mapping[str, Provider], usage: UsageStore | None = None) -> None:
        self._providers = dict(providers)
        self._usage = usage

    def configure(self, config: Config) -> None:
        """Pass a (re)loaded config to every provider that takes one."""
        for name, provider in self._providers.items():
            configure = getattr(provider, "configure", None)
            if configure is None:
                continue
            try:
                configure(config)
            except Exception:
                log.exception("provider %r failed to apply the config", name)

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
        if self._usage is not None:
            results = [
                replace(r, score=r.score * (1 + FRECENCY_WEIGHT * self._usage.boost(r.id)))
                if r.learn
                else r
                for r in results
            ]
        fallbacks = [r for r in results if r.fallback][:limit]
        regular = [r for r in results if not r.fallback]
        regular.sort(key=lambda r: r.score, reverse=True)  # stable: keeps provider order on ties
        return regular[: limit - len(fallbacks)] + fallbacks

    def record(self, result: Result) -> None:
        if self._usage is not None and result.learn:
            self._usage.record(result.id)
