"""Types shared by all providers.

Providers stay free of GTK imports so they can be tested without a display. Anything
that needs the running app (launching, reloading, quitting) goes through Host.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Result:
    id: str  # stable key, e.g. "command:quit"; used for frecency from M1 on
    title: str
    subtitle: str = ""
    icon: str | None = None  # themed icon name or absolute path
    action: Callable[[], None] | None = None
    alt_action: Callable[[], None] | None = None  # Alt+Enter
    score: float = 0.0


class Host(Protocol):
    def reload_config(self) -> None: ...
    def open_config(self) -> None: ...
    def quit_launcher(self) -> None: ...


class Provider(Protocol):
    name: str

    def query(self, text: str) -> list[Result]: ...
