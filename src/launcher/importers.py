"""Imports: Ulauncher shortcuts (-> [[quicklink]] TOML) and espanso matches (-> snippets)."""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import paths
from .config import Snippet
from .config_writer import ConfigWriter, write_atomic

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


# --- espanso -------------------------------------------------------------------------

_ESPANSO_VAR = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
# Keys a match may have and still become a snippet; anything else (word, regex, forms,
# markdown, image_path, ...) has no equivalent here, so such matches stay in espanso.
_SIMPLE_KEYS = {"trigger", "triggers", "replace", "vars", "label"}


def espanso_to_snippet(match: Any) -> Snippet | None:
    """A launcher snippet for one espanso match, or None if it can't be converted."""
    if not isinstance(match, dict) or not set(match) <= _SIMPLE_KEYS:
        return None
    triggers = match.get("triggers") or [match.get("trigger")]
    text = match.get("replace")
    if len(triggers) != 1 or not isinstance(triggers[0], str) or not isinstance(text, str):
        return None
    if not text or any(ch.isspace() for ch in triggers[0]) or len(triggers[0]) < 2:
        return None
    variables = {}
    for var in match.get("vars") or []:
        if not isinstance(var, dict):
            return None
        kind, name = var.get("type"), var.get("name")
        if kind == "clipboard":
            variables[name] = "{clipboard}"
        elif kind == "date" and isinstance((var.get("params") or {}).get("format"), str):
            variables[name] = "{date:" + var["params"]["format"] + "}"
        else:
            return None  # shell, script, choice, form, ... run code or ask questions

    unknown = False

    def substitute(m: re.Match) -> str:
        nonlocal unknown
        if m.group(1) not in variables:
            unknown = True  # e.g. a global var defined elsewhere
        return variables.get(m.group(1), m.group(0))

    body = _ESPANSO_VAR.sub(substitute, text).replace("$|$", "{cursor}")
    if unknown:
        return None
    return Snippet(name=_snippet_name(match, triggers[0], body), body=body, trigger=triggers[0])


def _snippet_name(match: dict, trigger: str, body: str) -> str:
    if isinstance(match.get("label"), str) and match["label"].strip():
        return match["label"].strip()
    first = body.strip().splitlines()[0] if body.strip() else ""
    if not first or "{" in first:
        return trigger
    return first if len(first) <= 40 else first[:39] + "…"


@dataclass
class EspansoImport:
    snippets: list[Snippet]
    kept: list[Any]  # matches that stay in espanso's file
    data: dict  # the whole parsed file, to write back without the imported matches


def espanso_base() -> Path:
    return paths.espanso_match_dir() / "base.yml"


def read_espanso(path: Path, existing: tuple[Snippet, ...] = ()) -> EspansoImport:
    """Split an espanso match file into importable snippets and matches to keep.

    Matches whose trigger or name clashes with an existing snippet stay in espanso."""
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not an espanso match file")
    names = {s.name.casefold() for s in existing}
    triggers = {s.trigger for s in existing if s.trigger}
    snippets, kept = [], []
    for match in data.get("matches") or []:
        snippet = espanso_to_snippet(match)
        if snippet is None or snippet.trigger in triggers:
            kept.append(match)
            continue
        name, n = snippet.name, 2
        while name.casefold() in names:
            name, n = f"{snippet.name} ({n})", n + 1
        names.add(name.casefold())
        triggers.add(snippet.trigger)
        snippets.append(replace(snippet, name=name))
    return EspansoImport(snippets, kept, data)


def remaining_espanso_yaml(result: EspansoImport) -> str:
    import yaml

    data = dict(result.data)
    data["matches"] = result.kept
    return (
        "# Matches the launcher imported were moved to ~/.config/launcher/snippets.toml;\n"
        "# the original file is next to this one as base.yml.bak.\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    )


def import_espanso(writer: ConfigWriter, path: Path) -> list[Snippet]:
    """Move espanso's simple matches into snippets.toml. The launcher then writes them
    back to espanso as launcher.yml, so every trigger keeps working. The original file
    is kept as <name>.bak."""
    result = read_espanso(path, writer.load().snippets)
    if not result.snippets:
        return []
    writer.add_snippets(result.snippets)  # validates first; raises ConfigError on clashes
    backup = path.with_name(path.name + ".bak")
    if backup.exists():
        backup = path.with_name(f"{path.name}.{int(time.time())}.bak")
    shutil.copy2(path, backup)
    write_atomic(path, remaining_espanso_yaml(result))
    return result.snippets
