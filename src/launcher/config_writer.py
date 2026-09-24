"""Safe, comment-preserving edits to config.toml (used by Launcher Settings).

Every edit re-reads the file (so changes made in a text editor meanwhile are kept),
applies the change with tomlkit (which keeps comments and layout), validates the result
with the same parser the launcher uses, and only then replaces the file atomically.
An edit that would produce an invalid config raises ConfigError and writes nothing.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import AoT, String, StringType, Table, Trivia

from .config import (
    SNIPPETS_HEADER,
    AppSettings,
    Config,
    ConfigError,
    QuickLink,
    Snippet,
    ensure_config,
    parse_texts,
    snippets_file,
)

Edit = Callable[[tomlkit.TOMLDocument], None]


class ConfigWriter:
    """Edits config.toml, or snippets.toml (next to it) with snippets=True."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.snippets_path = snippets_file(path)

    def load(self) -> Config:
        ensure_config(self.path)
        config, _warnings = parse_texts(_read(self.path), _read(self.snippets_path))
        return config

    def edit(self, change: Edit, snippets: bool = False) -> Config:
        ensure_config(self.path)
        path = self.snippets_path if snippets else self.path
        original = _read(path)
        if original is None:  # only snippets.toml can be missing
            original = SNIPPETS_HEADER
        doc = tomlkit.parse(original)
        change(doc)
        text = tomlkit.dumps(doc).rstrip("\n") + "\n"  # removed tables leave blank lines
        # Validate together with the other file: aliases and hotkeys span both.
        if snippets:
            config, _warnings = parse_texts(_read(self.path), text)
        else:
            config, _warnings = parse_texts(text, _read(self.snippets_path))
        write_atomic(path, text)
        return config

    # --- typed helpers ---------------------------------------------------------------

    def set_value(self, section: str, key: str, value: Any) -> Config:
        def change(doc: tomlkit.TOMLDocument) -> None:
            table = _table(doc, section)
            _put(table, key, _array(value) if isinstance(value, (list, tuple)) else value)

        return self.edit(change)

    def set_app(self, app_id: str, settings: AppSettings) -> Config:
        """Set an app's alias/hotkey; an app with neither is removed from the file."""

        def change(doc: tomlkit.TOMLDocument) -> None:
            apps = doc.get("apps")
            if settings == AppSettings():
                if apps is not None and app_id in apps:
                    del apps[app_id]
                    if not apps:
                        del doc["apps"]
                return
            if apps is None:
                apps = tomlkit.table(is_super_table=True)
                doc["apps"] = apps
            table = apps.get(app_id)
            if table is None:
                table = tomlkit.table()
                apps[app_id] = table
            _set_or_drop(table, "alias", settings.alias)
            _set_or_drop(table, "hotkey", settings.hotkey)

        return self.edit(change)

    def save_quicklink(self, original_name: str | None, link: QuickLink) -> Config:
        """Update the quicklink named `original_name`, or append a new one if None."""

        def change(doc: tomlkit.TOMLDocument) -> None:
            links = _aot(doc, "quicklink")
            if original_name is None:
                table = tomlkit.table()
                links.append(table)
            else:
                table = links[_find(links, original_name, "quicklink")]
            for f in fields(QuickLink):
                value = getattr(link, f.name)
                if f.name in ("name", "url"):
                    _put(table, f.name, value)
                else:
                    _set_or_drop(table, f.name, value)

        return self.edit(change)

    def delete_quicklink(self, name: str) -> Config:
        def change(doc: tomlkit.TOMLDocument) -> None:
            links = _aot(doc, "quicklink")
            del links[_find(links, name, "quicklink")]

        return self.edit(change)

    def move_quicklink(self, name: str, delta: int) -> Config:
        """Move up (-1) or down (+1); order decides the order of fallback searches."""

        def change(doc: tomlkit.TOMLDocument) -> None:
            links = _aot(doc, "quicklink")
            i = _find(links, name, "quicklink")
            j = max(0, min(len(links) - 1, i + delta))
            if i != j:
                item = links[i]
                del links[i]
                links.insert(j, item)

        return self.edit(change)

    def save_snippet(self, original_name: str | None, snippet: Snippet) -> Config:
        """Update the snippet named `original_name`, or append a new one if None."""

        def change(doc: tomlkit.TOMLDocument) -> None:
            snippets = _aot(doc, "snippet")
            if original_name is None:
                table = tomlkit.table()
                snippets.append(table)
            else:
                table = snippets[_find(snippets, original_name, "snippet")]
            _put(table, "name", snippet.name)
            for key in ("trigger", "alias", "hotkey"):
                _set_or_drop(table, key, getattr(snippet, key))
            if "body" in table:
                del table["body"]  # keep the body last: it may span many lines
            table["body"] = body_string(snippet.body)

        return self.edit(change, snippets=True)

    def add_snippets(self, snippets: list[Snippet]) -> Config:
        """Append several snippets in one write (used by the espanso import)."""

        def change(doc: tomlkit.TOMLDocument) -> None:
            tables = _aot(doc, "snippet")
            for snippet in snippets:
                table = tomlkit.table()
                table["name"] = snippet.name
                for key in ("trigger", "alias", "hotkey"):
                    if value := getattr(snippet, key):
                        table[key] = value
                table["body"] = body_string(snippet.body)
                tables.append(table)

        return self.edit(change, snippets=True)

    def delete_snippet(self, name: str) -> Config:
        def change(doc: tomlkit.TOMLDocument) -> None:
            snippets = _aot(doc, "snippet")
            del snippets[_find(snippets, name, "snippet")]

        return self.edit(change, snippets=True)


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def body_string(body: str) -> tomlkit.items.String:
    """Multi-line bodies become triple-quoted strings, so they stay readable."""
    if "\n" not in body:
        return tomlkit.string(body)
    plain = tomlkit.string(body, multiline=True)
    if body.startswith("\n"):
        return plain  # tomlkit already writes the opening quotes on a line of their own
    # TOML drops a newline right after the opening quotes, so start the text on the
    # next line: body = """⏎Best regards,⏎Jinwei""" rather than """Best regards,…
    try:
        return String(StringType.MLB, body, "\n" + plain.as_string()[3:-3], Trivia())
    except Exception:  # private API changed: the plain form is still correct
        return plain


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e


def _aot(doc: tomlkit.TOMLDocument, key: str) -> AoT:
    items = doc.get(key)
    if items is None:
        items = tomlkit.aot()
        doc[key] = items
    return items


def _find(items: AoT, name: str, kind: str) -> int:
    for i, table in enumerate(items):
        if str(table.get("name", "")).casefold() == name.casefold():
            return i
    raise ConfigError(f"{kind} '{name}' no longer exists")


def _table(doc: tomlkit.TOMLDocument, section: str) -> Table:
    table = doc.get(section)
    if table is None:
        table = tomlkit.table()
        doc[section] = table
    return table


def _set_or_drop(table: Table, key: str, value: Any) -> None:
    """Write a value, or remove the key when it is the default (empty / False)."""
    if value in ("", False, None):
        if key in table:
            del table[key]
    else:
        _put(table, key, value)


def _put(table: Table, key: str, value: Any) -> None:
    """Set a key; a new key goes right after the table's last key, not after the
    comments and blank lines that trail it (those usually introduce the next section)."""
    if key in table:
        table[key] = value
        return
    last_key = None
    for existing, _item in table.value.body:
        if existing is not None:
            last_key = existing
    try:
        if last_key is None:
            raise AttributeError
        table.value._insert_after(last_key, key, tomlkit.item(value))  # noqa: SLF001
    except (AttributeError, KeyError, TypeError):  # private API changed: plain append
        table[key] = value


def _array(values) -> tomlkit.items.Array:
    array = tomlkit.array()
    array.extend(values)
    if len(values) > 3:
        array.multiline(True)
    return array
