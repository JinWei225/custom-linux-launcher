"""Snippets from snippets.toml: search them and paste one into the window you came from.

In snippets mode (browse=True) an empty search lists every snippet; in the main
launcher they only show up for a search.
"""

from __future__ import annotations

from ..config import Config, Snippet
from ..ranking import best_score
from ..snippets import CURSOR_MARK, preview_text
from .base import ALIAS_SCORE, FALLBACK_SCORE, Host, Result

ICON = "insert-text"
# A search that only matches inside the body ranks below any name match.
BODY_SCORE = 0.25


class SnippetsProvider:
    name = "snippets"

    def __init__(self, host: Host, browse: bool) -> None:
        self._host = host
        self._browse = browse
        self._snippets: tuple[Snippet, ...] = ()

    def configure(self, config: Config) -> None:
        self._snippets = config.snippets

    def query(self, text: str) -> list[Result]:
        query = text.strip()
        if not query:
            if not self._browse:
                return []
            if not self._snippets:
                return [self._new_result("")]
            # Config order; the engine's frecency boost floats the ones you use up.
            return [self._result(s, 1.0 - i * 1e-4) for i, s in enumerate(self._snippets)]
        results = []
        for snippet in self._snippets:
            score = self._score(snippet, query)
            if score is not None:
                results.append(self._result(snippet, score))
        if self._browse and not any(r.score >= 1.0 for r in results):
            results.append(self._new_result(query))
        return results

    def _new_result(self, name: str) -> Result:
        return Result(
            id="snippet-new",
            title=f"New Snippet “{name}”…" if name else "New Snippet…",
            subtitle="Opens Launcher Settings",
            icon="list-add",
            action=lambda: self._host.open_settings(f"snippet:{name}"),
            learn=False,
            fallback=True,
            score=FALLBACK_SCORE,
        )

    def _score(self, snippet: Snippet, query: str) -> float | None:
        if snippet.alias and query.casefold() == snippet.alias.casefold():
            return ALIAS_SCORE
        score = best_score(query, (snippet.name, snippet.trigger, snippet.alias))
        if score is None and self._browse and query.casefold() in snippet.body.casefold():
            score = BODY_SCORE
        return score

    def _result(self, snippet: Snippet, score: float) -> Result:
        body = preview_text(snippet.body)
        plain = body.replace(CURSOR_MARK, "")
        first = next((line.strip() for line in plain.splitlines() if line.strip()), "")
        parts = [p for p in (snippet.trigger, f"“{snippet.alias}”" if snippet.alias else "") if p]
        parts.append(first if len(first) <= 80 else first[:79] + "…")
        name = snippet.name
        return Result(
            id=f"snippet:{name}",
            title=name,
            subtitle=" · ".join(parts),
            icon=ICON,
            action=lambda: self._host.paste_snippet(name),
            alt_action=lambda: self._host.copy_snippet(name),
            preview=("text", body),
            score=score,
        )
