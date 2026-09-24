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


class _ClipboardPauseCommand:
    """Title depends on the current state, so it is built per query."""

    @staticmethod
    def command(host: Host) -> _Command:
        paused = host.clipboard_paused()
        return _Command(
            "clipboard-pause",
            "Resume Clipboard Recording" if paused else "Pause Clipboard Recording",
            "Recording is paused" if paused else "Stop saving what you copy until resumed",
            "media-playback-start-symbolic" if paused else "media-playback-pause-symbolic",
            ("clipboard", "privacy", "pause", "resume", "incognito"),
            lambda h: h.toggle_clipboard_pause(),
        )


_CLEAR_CLIPBOARD = _Command(
    "clipboard-clear",
    "Clear Clipboard History",
    "Delete every entry except pinned ones",
    "edit-clear-all-symbolic",
    ("clipboard", "delete", "forget", "wipe"),
    lambda host: host.clear_clipboard(),
)


class CommandsProvider:
    name = "commands"

    def __init__(self, host: Host) -> None:
        self._host = host

    def query(self, text: str) -> list[Result]:
        if not text.strip():
            return []
        results = []
        for cmd in (*_COMMANDS, _ClipboardPauseCommand.command(self._host), _CLEAR_CLIPBOARD):
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
