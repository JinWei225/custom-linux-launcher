"""Convert Ulauncher shortcuts (~/.config/ulauncher/shortcuts.json) to [[quicklink]] TOML."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ULAUNCHER_SHORTCUTS = Path.home() / ".config/ulauncher/shortcuts.json"


def _toml_string(value: str) -> str:
    # JSON string syntax is valid TOML basic-string syntax.
    return json.dumps(value, ensure_ascii=False)


def ulauncher_to_toml(data: Any) -> str:
    shortcuts = data.values() if isinstance(data, dict) else data
    blocks = ["# Imported from Ulauncher"]
    for s in shortcuts:
        url = str(s.get("cmd", "")).strip()
        name = str(s.get("name", "")).strip()
        if not url or not name or "://" not in url:
            continue  # Ulauncher also allows shell scripts; those have no quicklink equivalent
        url = url.replace("%s", "{query}")
        lines = ["[[quicklink]]", f"name = {_toml_string(name)}"]
        if keyword := str(s.get("keyword", "")).strip():
            lines.append(f"alias = {_toml_string(keyword)}")
        lines.append(f"url = {_toml_string(url)}")
        if s.get("is_default_search") and "{query}" in url:
            lines.append("fallback = true")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def import_ulauncher(path: Path = ULAUNCHER_SHORTCUTS) -> str:
    return ulauncher_to_toml(json.loads(path.read_text(encoding="utf-8")))
