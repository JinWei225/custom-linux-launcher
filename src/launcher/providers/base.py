"""Types shared by all providers.

Providers stay free of GTK imports so they can be tested without a display. Anything
that needs the running app (launching, reloading, quitting) goes through Host.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

# Score for an exact alias match: above any fuzzy match (which tops out at 1.0).
ALIAS_SCORE = 2.0
# Score for "search the web for …" fallbacks: below any real match.
FALLBACK_SCORE = 0.001


@dataclass
class Result:
    id: str  # stable key used for frecency, e.g. "app:firefox.desktop"
    title: str
    subtitle: str = ""
    icon: str | None = None  # themed icon name, serialized GIcon, or absolute path
    action: Callable[[], None] | None = None
    alt_action: Callable[[], None] | None = None  # Alt+Enter
    completion: str | None = None  # text Tab puts in the search box, e.g. "g "
    learn: bool = True  # record picks for frecency (off for one-off results)
    fallback: bool = False  # always listed last, and never cut off by the result limit
    score: float = 0.0


class Host(Protocol):
    def reload_config(self) -> None: ...
    def open_config(self) -> None: ...
    def quit_launcher(self) -> None: ...
    def launch_app(self, app_id: str) -> None: ...
    def open_uri(self, uri: str) -> None: ...
    def copy_text(self, text: str) -> None: ...
    def open_file(self, path: str) -> None: ...
    def reveal_file(self, path: str) -> None: ...
    def open_settings(self, edit: str | None = None) -> None: ...


class Provider(Protocol):
    name: str

    def query(self, text: str) -> list[Result]: ...
