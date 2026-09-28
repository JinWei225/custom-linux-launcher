"""Light or dark: `[ui] appearance` applied to this process's windows.

"system" follows GNOME's Style setting (and changes with it); "light" and "dark"
override it. The launcher, Launcher Settings and Notes each apply it on start and
whenever the config file changes.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

SCHEMES = {
    "system": Adw.ColorScheme.DEFAULT,  # the default style manager: follow GNOME
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
}


def apply(appearance: str) -> None:
    scheme = SCHEMES.get(appearance, Adw.ColorScheme.DEFAULT)
    manager = Adw.StyleManager.get_default()
    if manager.get_color_scheme() != scheme:
        manager.set_color_scheme(scheme)
