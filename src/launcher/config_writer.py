"""Safe, comment-preserving edits to config.toml (used by Launcher Settings).

Every edit re-reads the file (so changes made in a text editor meanwhile are kept),
applies the change with tomlkit (which keeps comments and layout), validates the result
with the same parser the launcher uses, and only then replaces the file atomically.
An edit that would produce an invalid config raises ConfigError and writes nothing.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import AoT, Table

from .config import (
    AppSettings,
    Config,
    ConfigError,
    QuickLink,
    ensure_config,
    parse_config,
)

Edit = Callable[[tomlkit.TOMLDocument], None]


class ConfigWriter:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Config:
        ensure_config(self.path)
        return self._validate(self.path.read_text(encoding="utf-8"))

    def edit(self, change: Edit) -> Config:
        ensure_config(self.path)
        doc = tomlkit.parse(self.path.read_text(encoding="utf-8"))
        change(doc)
        text = tomlkit.dumps(doc).rstrip("\n") + "\n"  # removed tables leave blank lines
        config = self._validate(text)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)
        return config

    @staticmethod
    def _validate(text: str) -> Config:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(str(e)) from e
        config, _warnings = parse_config(data)
        return config

    # --- typed helpers ---------------------------------------------------------------

    def set_value(self, section: str, key: str, value: Any) -> Config:
        def change(doc: tomlkit.TOMLDocument) -> None:
            table = _table(doc, section)
            if isinstance(value, (list, tuple)):
                table[key] = _array(value)
            else:
                table[key] = value

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
            links = _links(doc)
            if original_name is None:
                table = tomlkit.table()
                links.append(table)
            else:
                table = links[_find_link(links, original_name)]
            for f in fields(QuickLink):
                value = getattr(link, f.name)
                if f.name in ("name", "url"):
                    table[f.name] = value
                else:
                    _set_or_drop(table, f.name, value)

        return self.edit(change)

    def delete_quicklink(self, name: str) -> Config:
        def change(doc: tomlkit.TOMLDocument) -> None:
            links = _links(doc)
            del links[_find_link(links, name)]

        return self.edit(change)

    def move_quicklink(self, name: str, delta: int) -> Config:
        """Move up (-1) or down (+1); order decides the order of fallback searches."""

        def change(doc: tomlkit.TOMLDocument) -> None:
            links = _links(doc)
            i = _find_link(links, name)
            j = max(0, min(len(links) - 1, i + delta))
            if i != j:
                item = links[i]
                del links[i]
                links.insert(j, item)

        return self.edit(change)


def _table(doc: tomlkit.TOMLDocument, section: str) -> Table:
    table = doc.get(section)
    if table is None:
        table = tomlkit.table()
        doc[section] = table
    return table


def _links(doc: tomlkit.TOMLDocument) -> AoT:
    links = doc.get("quicklink")
    if links is None:
        links = tomlkit.aot()
        doc["quicklink"] = links
    return links


def _find_link(links: AoT, name: str) -> int:
    for i, table in enumerate(links):
        if str(table.get("name", "")).casefold() == name.casefold():
            return i
    raise ConfigError(f"quicklink '{name}' no longer exists in the config file")


def _set_or_drop(table: Table, key: str, value: Any) -> None:
    """Write a value, or remove the key when it is the default (empty / False)."""
    if value in ("", False, None):
        if key in table:
            del table[key]
    else:
        table[key] = value


def _array(values) -> tomlkit.items.Array:
    array = tomlkit.array()
    array.extend(values)
    if len(values) > 3:
        array.multiline(True)
    return array
