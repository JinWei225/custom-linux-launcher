"""Built-in launcher commands (reload config, open config, quit)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..ranking import best_score
from .base import Host, Result


@dataclass(frozen=True)
class _Command:
    key: str
    title: str
    subtitle: str
    icon: str
    keywords: tuple[str, ...]
    run: Callable[[Host], None]


_COMMANDS = (
    _Command(
        "settings",
        "Launcher Settings",
        "Shortcuts, aliases, quicklinks and file search folders",
        "preferences-system",
        ("launcher settings", "preferences", "hotkeys", "aliases", "quicklinks"),
        lambda host: host.open_settings(),
    ),
    _Command(
        "reload",
        "Reload Launcher Config",
        "Re-read ~/.config/launcher/config.toml",
        "view-refresh-symbolic",
        ("launcher reload", "refresh"),
        lambda host: host.reload_config(),
    ),
    _Command(
        "open-config",
        "Open Launcher Config File",
        "Edit ~/.config/launcher/config.toml in a text editor",
        "document-edit-symbolic",
        ("config", "toml"),
        lambda host: host.open_config(),
    ),
    _Command(
        "quit",
        "Quit Launcher",
        "Stop the launcher (the systemd service will not restart it)",
        "application-exit-symbolic",
        # No "launcher ..." keyword: typing "launcher" must not put Quit first.
        ("exit",),
        lambda host: host.quit_launcher(),
    ),
)


class CommandsProvider:
    name = "commands"

    def __init__(self, host: Host) -> None:
        self._host = host

    def query(self, text: str) -> list[Result]:
        if not text.strip():
            return []
        results = []
        for cmd in _COMMANDS:
            score = best_score(text, (cmd.title, *cmd.keywords))
            if score is None:
                continue
            results.append(
                Result(
                    id=f"command:{cmd.key}",
                    title=cmd.title,
                    subtitle=cmd.subtitle,
                    icon=cmd.icon,
                    action=lambda cmd=cmd: cmd.run(self._host),
                    score=score,
                )
            )
        return results
