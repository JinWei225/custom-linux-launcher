"""Installed applications (.desktop files, including Flatpak and Snap)."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from ..config import Config
from ..ranking import fuzzy_score
from .base import ALIAS_SCORE, Host, Result

log = logging.getLogger(__name__)

# Secondary fields (generic name, keywords) count for less than the app's own name.
SECONDARY_WEIGHT = 0.8
# With an empty query (apps mode), list apps ordered purely by frecency.
EMPTY_QUERY_SCORE = 0.01


@dataclass(frozen=True)
class AppEntry:
    id: str  # desktop file id, e.g. "org.gnome.Nautilus.desktop"
    name: str
    generic_name: str = ""
    description: str = ""
    executable: str = ""
    keywords: tuple[str, ...] = ()
    icon: str | None = None


def load_apps() -> list[AppEntry]:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    apps = []
    for info in Gio.AppInfo.get_all():
        if not info.should_show() or not info.get_id():
            continue
        get = getattr(info, "get_generic_name", None)
        keywords = getattr(info, "get_keywords", lambda: [])() or []
        icon = info.get_icon()
        apps.append(
            AppEntry(
                id=info.get_id(),
                name=info.get_display_name() or info.get_name(),
                generic_name=(get() if get else "") or "",
                description=info.get_description() or "",
                executable=os.path.basename(info.get_executable() or ""),
                keywords=tuple(keywords),
                icon=icon.to_string() if icon else None,
            )
        )
    apps.sort(key=lambda a: a.name.casefold())
    return apps


class AppsProvider:
    name = "apps"

    def __init__(self, host: Host, loader: Callable[[], list[AppEntry]] = load_apps) -> None:
        self._host = host
        self._loader = loader
        self._apps: list[AppEntry] | None = None  # loaded lazily, dropped when apps change
        self._aliases: dict[str, AppEntry] = {}
        self._alias_targets: dict[str, str] = {}

    def configure(self, config: Config) -> None:
        # alias -> desktop id (or, for hand-written entries, an app name)
        self._alias_targets = {
            app.alias.casefold(): app_id for app_id, app in config.apps.items() if app.alias
        }
        self._resolve_aliases()

    def invalidate(self) -> None:
        """Call when installed apps change; the list is rebuilt on the next query."""
        self._apps = None

    def query(self, text: str) -> list[Result]:
        apps = self._ensure_loaded()
        query = text.strip()
        if not query:
            return [self._result(app, EMPTY_QUERY_SCORE) for app in apps]

        alias_app = self._aliases.get(query.casefold())
        results = []
        for app in apps:
            if app is alias_app:
                results.append(self._result(app, ALIAS_SCORE, alias=query))
                continue
            score = _match(query, app)
            if score is not None:
                results.append(self._result(app, score))
        return results

    def _ensure_loaded(self) -> list[AppEntry]:
        if self._apps is None:
            self._apps = self._loader()
            log.debug("loaded %d apps", len(self._apps))
            self._resolve_aliases()
        return self._apps

    def _resolve_aliases(self) -> None:
        if self._apps is None:
            return  # resolved on first load
        by_id = {a.id.casefold(): a for a in self._apps}
        by_name = {a.name.casefold(): a for a in self._apps}
        self._aliases = {}
        for alias, target in self._alias_targets.items():
            key = target.casefold()
            app = by_id.get(key) or by_id.get(key + ".desktop") or by_name.get(key)
            if app is None:
                log.warning("app alias %r: no installed app matches %r", alias, target)
                continue
            self._aliases[alias] = app

    def _result(self, app: AppEntry, score: float, alias: str | None = None) -> Result:
        subtitle = app.description or app.generic_name
        if alias:
            subtitle = f"{alias} → {subtitle}" if subtitle else alias
        return Result(
            id=f"app:{app.id}",
            title=app.name,
            subtitle=subtitle,
            icon=app.icon or "application-x-executable",
            action=lambda: self._host.launch_app(app.id),
            score=score,
        )


# Secondary fields only count on a real substring match; scattered-letter matches against
# long keyword lists would otherwise pull in almost every app for short queries.
SECONDARY_MIN_SCORE = 0.6


def _match(query: str, app: AppEntry) -> float | None:
    scores = []
    for text, weight in ((app.name, 1.0), (app.executable, 0.9)):
        if text and (s := fuzzy_score(query, text)) is not None:
            scores.append(s * weight)
    for text in (app.generic_name, *app.keywords):
        s = fuzzy_score(query, text) if text else None
        if s is not None and s >= SECONDARY_MIN_SCORE:
            scores.append(s * SECONDARY_WEIGHT)
    return max(scores, default=None)
