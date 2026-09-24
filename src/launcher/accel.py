"""Keyboard shortcut strings in GNOME/GTK accelerator syntax ("<Super><Shift>f").

Pure Python so the config validator can use it. `normalize` gives one canonical
spelling, so "<Shift><Super>F", "<super><shift>f" and "<Super><Shift>f" compare equal
(GNOME's own settings use all of these styles).
"""

from __future__ import annotations

import re

# Canonical modifier names in canonical order, with the aliases GTK accepts.
_MODIFIERS = {
    "control": "Control",
    "ctrl": "Control",
    "ctl": "Control",
    "primary": "Control",
    "alt": "Alt",
    "mod1": "Alt",
    "shift": "Shift",
    "shft": "Shift",
    "super": "Super",
    "mod4": "Super",
    "meta": "Meta",
    "hyper": "Hyper",
}
_ORDER = ("Control", "Alt", "Shift", "Super", "Meta", "Hyper")
_TOKEN = re.compile(r"<([A-Za-z0-9_]+)>")
_KEY = re.compile(r"[A-Za-z0-9_]+")
# Modifiers that make a shortcut "global-safe": Shift alone would steal normal typing.
_REAL_MODIFIERS = {"Control", "Alt", "Super", "Meta", "Hyper"}
_FUNCTION_KEY = re.compile(r"F([1-9]|1[0-9]|2[0-4])")


def parse(accel: str) -> tuple[frozenset[str], str] | None:
    """("<Super><Shift>f") -> ({"Super", "Shift"}, "f"); None if not an accelerator."""
    text = accel.strip()
    mods: set[str] = set()
    pos = 0
    while (m := _TOKEN.match(text, pos)) is not None:
        name = _MODIFIERS.get(m.group(1).lower())
        if name is None:
            return None
        mods.add(name)
        pos = m.end()
    key = text[pos:]
    if not _KEY.fullmatch(key):
        return None
    return frozenset(mods), key


def normalize(accel: str) -> str | None:
    parsed = parse(accel)
    if parsed is None:
        return None
    mods, key = parsed
    # Letter keys are case-insensitive in accelerators; named keys keep their spelling
    # but compare case-insensitively too ("Return" == "return").
    key = key.lower() if len(key) == 1 else key[0].upper() + key[1:]
    if _FUNCTION_KEY.fullmatch(key.upper()):
        key = key.upper()
    return "".join(f"<{m}>" for m in _ORDER if m in mods) + key


def same(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    return na is not None and nb is not None and na.casefold() == nb.casefold()


def hotkey_problem(accel: str) -> str | None:
    """Why this can't be used as a global hotkey, or None if it is fine."""
    parsed = parse(accel)
    if parsed is None:
        return f"{accel!r} is not a shortcut (expected something like <Super><Shift>f)"
    mods, key = parsed
    if not mods & _REAL_MODIFIERS and not _FUNCTION_KEY.fullmatch(key.upper()):
        return f"{accel!r} needs Super, Ctrl or Alt, or it would block normal typing"
    return None
