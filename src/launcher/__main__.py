"""Command line entry point.

launcher                    toggle the main launcher
launcher --mode clipboard   toggle a specific mode
launcher --show / --hide    show or hide without toggling
launcher --reload | --quit  control the running daemon
launcher --daemon           start in the background (used by the systemd unit)
launcher --import-ulauncher print Ulauncher shortcuts as [[quicklink]] TOML
launcher --run app:ID       launch an app / quicklink (what hotkeys run)
launcher --settings [--edit app:ID|quicklink:NAME]   open Launcher Settings
"""

from __future__ import annotations

import argparse
import logging
import sys

from .engine import MODES


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="launcher", description="GNOME launcher")
    parser.add_argument("--mode", choices=list(MODES), default="all")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show", action="store_true", help="show instead of toggling")
    group.add_argument("--hide", action="store_true")
    group.add_argument("--reload", action="store_true", help="reload the config file")
    group.add_argument("--quit", action="store_true", help="stop the running launcher")
    group.add_argument("--daemon", action="store_true", help="start hidden in the background")
    group.add_argument("--run", metavar="ITEM", help="run app:<desktop id> or quicklink:<name>")
    group.add_argument("--settings", action="store_true", help="open Launcher Settings")
    parser.add_argument("--edit", metavar="ITEM", help="with --settings: open this item")
    parser.add_argument("--debug-snapshots", metavar="DIR", help=argparse.SUPPRESS)
    group.add_argument(
        "--import-ulauncher",
        action="store_true",
        help="print Ulauncher shortcuts as [[quicklink]] entries for config.toml",
    )
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    return parser.parse_args(argv)


def requested_action(args: argparse.Namespace) -> tuple[str, str | None]:
    """The app action (name, parameter) that this invocation asks for."""
    for name in ("hide", "reload", "quit"):
        if getattr(args, name):
            return name, None
    if args.run:
        return "run", args.run
    return ("show" if args.show else "toggle"), args.mode


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.import_ulauncher:
        from .importers import import_ulauncher

        print(import_ulauncher(), end="")
        return 0

    if args.settings:
        from .settings.app import run_settings

        return run_settings(args.edit, snapshots=args.debug_snapshots)

    action, param = requested_action(args)

    if not args.daemon:
        from .client import SendStatus, send

        status = send(action, param)
        if status is SendStatus.SENT:
            return 0
        if status is SendStatus.TIMEOUT:
            print("launcher: the running instance is not responding", file=sys.stderr)
            return 1
        if action in ("hide", "reload", "quit"):
            return 0  # nothing is running, so there is nothing to do

    # No daemon is running (or we are the daemon): become the primary instance.
    from .app import run_primary

    # Nothing to toggle yet: a fresh instance shows itself (or runs the requested item).
    initial = None if args.daemon else ("run" if action == "run" else "show", param)
    return run_primary(initial, debug=args.debug)


if __name__ == "__main__":
    sys.exit(main())
