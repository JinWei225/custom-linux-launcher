"""Quicklinks from the config: plain URLs and {query} searches, with aliases.

gh            -> GitHub (exact alias, ranked first)
g cats        -> Google Search with {query} = "cats"
git           -> fuzzy match on name and alias
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote

from ..config import Config, QuickLink
from ..ranking import best_score
from .base import ALIAS_SCORE, FALLBACK_SCORE, Host, Result

LINK_ICON = "web-browser"
SEARCH_ICON = "system-search"


def fill_url(template: str, query: str) -> str:
    # quote(safe="") encodes spaces as %20, which works in both query strings and paths
    # (e.g. https://en.wikipedia.org/wiki/{query}).
    return template.replace("{query}", quote(query, safe=""))


def split_alias(text: str) -> tuple[str, str]:
    """ "g  some cats " -> ("g", "some cats")"""
    head, _, rest = text.strip().partition(" ")
    return head, rest.strip()


# url -> path of a cached website icon, or None
IconLookup = Callable[[str], "str | None"]


def _icon(link: QuickLink, lookup: IconLookup | None) -> str:
    if link.icon:
        return link.icon
    if lookup is not None and (path := lookup(link.url)):
        return path
    return SEARCH_ICON if "{query}" in link.url else LINK_ICON


class QuickLinksProvider:
    name = "quicklinks"

    def __init__(self, host: Host, icons: IconLookup | None = None) -> None:
        self._host = host
        self._icons = icons
        self._links: tuple[QuickLink, ...] = ()

    def configure(self, config: Config) -> None:
        self._links = config.quicklinks

    def query(self, text: str) -> list[Result]:
        if not text.strip():
            return []
        head, rest = split_alias(text)
        results = []
        for link in self._links:
            result = self._alias_result(link, head, rest) or self._fuzzy_result(link, text)
            if result is not None:
                results.append(result)
        return results

    def _alias_result(self, link: QuickLink, head: str, rest: str) -> Result | None:
        if not link.alias or head.casefold() != link.alias.casefold():
            return None
        is_search = "{query}" in link.url
        if rest and not is_search:
            return None  # "gh something" is not a match for a plain link
        if is_search and rest:
            return Result(
                id=f"quicklink:{link.name}",
                title=f"{link.name}: {rest}",
                subtitle=fill_url(link.url, rest),
                icon=_icon(link, self._icons),
                action=self._opener(fill_url(link.url, rest)),
                alt_action=self._copier(fill_url(link.url, rest)),
                score=ALIAS_SCORE,
            )
        return self._link_result(link, ALIAS_SCORE)

    def _fuzzy_result(self, link: QuickLink, text: str) -> Result | None:
        score = best_score(text, (link.name, link.alias))
        return None if score is None else self._link_result(link, score)

    def _link_result(self, link: QuickLink, score: float) -> Result:
        is_search = "{query}" in link.url
        alias = f"{link.alias} · " if link.alias else ""
        if is_search:
            hint = f"Type {link.alias} and a search, or press Tab" if link.alias else link.url
            subtitle = hint
        else:
            subtitle = alias + link.url
        return Result(
            id=f"quicklink:{link.name}",
            title=link.name,
            subtitle=subtitle,
            icon=_icon(link, self._icons),
            action=self._opener(fill_url(link.url, "")),
            alt_action=None if is_search else self._copier(link.url),
            completion=f"{link.alias} " if is_search and link.alias else None,
            score=score,
        )

    def _opener(self, url: str):
        return lambda: self._host.open_uri(url)

    def _copier(self, url: str):
        return lambda: self._host.copy_text(url)


class WebSearchProvider:
    """ "Search Google for …" rows under the results, for quicklinks with fallback = true."""

    name = "websearch"

    def __init__(self, host: Host, icons: IconLookup | None = None) -> None:
        self._host = host
        self._icons = icons
        self._links: tuple[QuickLink, ...] = ()
        self._search_aliases: set[str] = set()

    def configure(self, config: Config) -> None:
        self._links = tuple(link for link in config.quicklinks if link.fallback)
        self._search_aliases = {
            link.alias.casefold()
            for link in config.quicklinks
            if link.alias and "{query}" in link.url
        }

    def query(self, text: str) -> list[Result]:
        query = text.strip()
        if not query:
            return []
        head, rest = split_alias(query)
        if rest and head.casefold() in self._search_aliases:
            return []  # "g cats" is already handled by the Google quicklink itself
        return [
            Result(
                id=f"websearch:{link.name}",
                title=f"Search {link.name} for “{query}”",
                subtitle=fill_url(link.url, query),
                icon=_icon(link, self._icons),
                action=lambda link=link: self._host.open_uri(fill_url(link.url, query)),
                learn=False,  # never let a fallback climb above real matches
                fallback=True,
                score=FALLBACK_SCORE,
            )
            for link in self._links
        ]
