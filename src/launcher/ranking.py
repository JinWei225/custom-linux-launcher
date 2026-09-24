"""Fuzzy matching used by every provider.

Scores are in (0, 1]; None means "does not match". Rough bands:
  1.0         exact match
  0.8 - 0.9   prefix match
  0.7 - 0.8   substring starting at a word boundary
  0.6 - 0.7   other substring
  0.3 - 0.6   characters in order (subsequence); word initials score highest
"""

from __future__ import annotations

from collections.abc import Iterable


def fuzzy_score(query: str, text: str) -> float | None:
    q = query.casefold().strip()
    t = text.casefold()
    if not q:
        return 0.0
    if not t:
        return None
    if t == q:
        return 1.0

    tightness = len(q) / len(t)  # prefer shorter candidates for the same match
    pos = t.find(q)
    if pos == 0:
        return 0.8 + 0.1 * tightness
    if pos > 0:
        base = 0.7 if _is_boundary(t, pos) else 0.6
        # A later boundary match may exist even if the first occurrence is mid-word.
        if base == 0.6 and _find_boundary_substring(t, q) is not None:
            base = 0.7
        return base + 0.1 * tightness

    positions = _subsequence(q, t, prefer_boundaries=True) or _subsequence(
        q, t, prefer_boundaries=False
    )
    if positions is None:
        return None
    boundary_ratio = sum(_is_boundary(t, i) for i in positions) / len(q)
    spread = (positions[-1] - positions[0] + 1) / len(t)
    return 0.3 + 0.2 * boundary_ratio + 0.1 * (1 - spread) * tightness


def best_score(query: str, candidates: Iterable[str]) -> float | None:
    """Best score of the query against any candidate (title, alias, keywords...)."""
    scores = [s for c in candidates if c and (s := fuzzy_score(query, c)) is not None]
    return max(scores, default=None)


def _is_boundary(text: str, i: int) -> bool:
    return i == 0 or not text[i - 1].isalnum()


def _find_boundary_substring(t: str, q: str) -> int | None:
    start = t.find(q)
    while start != -1:
        if _is_boundary(t, start):
            return start
        start = t.find(q, start + 1)
    return None


def _subsequence(q: str, t: str, *, prefer_boundaries: bool) -> list[int] | None:
    positions: list[int] = []
    i = 0
    for ch in q:
        if ch.isspace():
            continue
        found = -1
        if prefer_boundaries:
            j = t.find(ch, i)
            while j != -1 and not _is_boundary(t, j):
                j = t.find(ch, j + 1)
            found = j
        if found == -1:
            found = t.find(ch, i)
        if found == -1:
            return None
        positions.append(found)
        i = found + 1
    return positions or None
