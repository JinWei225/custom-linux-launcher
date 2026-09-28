"""Notes on disk: one markdown file per note, sub-folders as notebooks.

Pure Python (no GTK) so it can be unit tested. Paths handed out are relative to the
notes folder ("FYP/Week 3.md"), so they stay short and survive the folder moving.

- The title is the note's first non-empty line, without a leading "# ".
- A note's file is named after its title. Saving renames the file only when the
  title was edited, so files made by other apps keep their own names.
- Pinned notes are listed in .notes.json in the notes folder.
- Files and folders starting with "." and "attachments" folders are not shown.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .notes_markdown import ATTACHMENTS, attachment_links

log = logging.getLogger(__name__)

UNTITLED = "Untitled"
PINS_FILE = ".notes.json"
MAX_NAME = 80


class NotesError(Exception):
    """An operation that can't be done (name taken, file gone...), with a message for
    the user."""


@dataclass(frozen=True)
class Note:
    rel: str  # "FYP/Week 3.md"
    title: str
    mtime: float

    @property
    def folder(self) -> str:
        parent = str(Path(self.rel).parent)
        return "" if parent == "." else parent


@dataclass
class Folder:
    rel: str  # "" for the notes folder itself
    name: str
    folders: list[Folder] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


def title_of(text: str) -> str:
    """ "# Lecture 3\\n..." -> "Lecture 3"; empty -> "Untitled"."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            line = re.sub(r"^#{1,6}(?:\s+|$)", "", line).strip()  # "# " mid-typing too
            line = re.sub(r"^(?:[-*+]|\d+\.)\s+(?:\[[ xX]?\]\s+)?", "", line).strip()
            return line[:MAX_NAME].strip() or UNTITLED
    return UNTITLED


def safe_name(title: str) -> str:
    """A file or folder name for a title: no slashes or control characters."""
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]+', "-", title).strip().lstrip(".").strip()
    name = " ".join(name.split())[:MAX_NAME].strip()
    return name or UNTITLED


def _visible(entry: os.DirEntry) -> bool:
    return not entry.name.startswith(".")


class NotesStore:
    def __init__(self, root: Path, trash: Callable[[Path], None] | None = None) -> None:
        self.root = root
        self._trash = trash or _delete_for_good

    def path(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        root = self.root.resolve()
        if path != root and root not in path.parents:
            raise NotesError(f"{rel!r} is outside the notes folder")
        return path

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    # --- reading -------------------------------------------------------------------------

    def tree(self) -> Folder:
        """Every folder and note. Folders by name, notes by title."""
        return self._scan(self.root, "", self.root.name)

    def _scan(self, directory: Path, rel: str, name: str) -> Folder:
        folder = Folder(rel, name)
        try:
            entries = [e for e in os.scandir(directory) if _visible(e)]
        except OSError as e:
            log.warning("cannot read %s: %s", directory, e)
            return folder
        for entry in entries:
            child = f"{rel}/{entry.name}" if rel else entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name != ATTACHMENTS:
                        folder.folders.append(self._scan(Path(entry.path), child, entry.name))
                elif entry.is_file() and entry.name.lower().endswith(".md"):
                    folder.notes.append(self._note(child, Path(entry.path)))
            except OSError:
                continue
        folder.folders.sort(key=lambda f: f.name.casefold())
        folder.notes.sort(key=lambda n: (n.title.casefold(), n.rel))
        return folder

    def _note(self, rel: str, path: Path) -> Note:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
        return Note(rel, title_of(head), path.stat().st_mtime)

    def note(self, rel: str) -> Note:
        path = self.path(rel)
        try:
            return self._note(rel, path)
        except OSError as e:
            raise NotesError(f"cannot open {rel}: {e.strerror or e}") from e

    def all_notes(self, tree: Folder | None = None) -> list[Note]:
        return flatten(tree or self.tree())

    def recent(self, limit: int = 10, tree: Folder | None = None) -> list[Note]:
        """The notes changed most recently, newest first."""
        return sorted(self.all_notes(tree), key=lambda n: n.mtime, reverse=True)[:limit]

    def folders(self, tree: Folder | None = None) -> list[str]:
        """Every folder's relative path ("" first), for "Move to…"."""
        out: list[str] = []

        def walk(folder: Folder) -> None:
            out.append(folder.rel)
            for sub in folder.folders:
                walk(sub)

        walk(tree or self.tree())
        return out

    def read(self, rel: str) -> str:
        try:
            return self.path(rel).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise NotesError(f"cannot open {rel}: {e.strerror or e}") from e

    def mtime(self, rel: str) -> float | None:
        try:
            return self.path(rel).stat().st_mtime
        except OSError:
            return None

    # --- pins ------------------------------------------------------------------------------

    def _pins_path(self) -> Path:
        return self.root / PINS_FILE

    def pins(self) -> list[str]:
        try:
            data = json.loads(self._pins_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        pinned = data.get("pinned", []) if isinstance(data, dict) else []
        return [p for p in pinned if isinstance(p, str)]

    def pinned(self) -> list[Note]:
        """Pinned notes that still exist, in the order they were pinned."""
        notes = []
        for rel in self.pins():
            try:
                if self.path(rel).is_file():
                    notes.append(self.note(rel))
            except NotesError:
                continue
        return notes

    def set_pinned(self, rel: str, pinned: bool) -> None:
        pins = [p for p in self.pins() if p != rel]
        if pinned:
            pins.append(rel)
        self._write_pins(pins)

    def _write_pins(self, pins: list[str]) -> None:
        self.ensure()
        _write_atomic(self._pins_path(), json.dumps({"pinned": pins}, indent=2) + "\n")

    def _repin(self, old: str, new: str, *, folder: bool = False) -> None:
        """Keep pins pointing at a note (or everything in a folder) after a move."""
        pins = self.pins()
        changed = []
        for p in pins:
            if p == old:
                p = new
            elif folder and p.startswith(old + "/"):
                p = new + p[len(old) :]
            changed.append(p)
        if changed != pins:
            self._write_pins(changed)

    # --- writing ---------------------------------------------------------------------------

    def create(self, folder: str = "", text: str = "") -> Note:
        """A new note in a folder, named after its title (or "Untitled")."""
        directory = self.path(folder)
        directory.mkdir(parents=True, exist_ok=True)
        target = self._free(directory, safe_name(title_of(text)))
        _write_atomic(target, text)
        return self.note(self._rel(target))

    def save(self, rel: str, text: str, *, rename: bool = False) -> str:
        """Write a note; returns its (possibly new) relative path.

        rename=True: the title was edited, so move the file to match it. Nothing is
        written if the file already holds this text."""
        path = self.path(rel)
        try:
            unchanged = path.read_text(encoding="utf-8") == text
        except (OSError, UnicodeDecodeError):
            unchanged = False
        if not unchanged:
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(path, text)
        if rename:
            name = safe_name(title_of(text))
            if path.stem != name:
                target = self._free(path.parent, name, exclude=path)
                os.rename(path, target)
                new = self._rel(target)
                self._repin(rel, new)
                return new
        return rel

    def rename_note(self, rel: str, title: str) -> str:
        """Change a note's title (its first line) and its file name."""
        title = " ".join(title.split())
        if not title:
            raise NotesError("a note needs a title")
        text = self.read(rel)
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.strip():
                heading = re.match(r"\s*(#{1,6}\s+)", line)
                prefix = heading.group(1) if heading else ""
                ending = line[len(line.rstrip("\r\n")) :]
                lines[i] = f"{prefix}{title}{ending}"
                break
        else:
            lines = [f"# {title}\n", *lines]
        return self.save(rel, "".join(lines), rename=True)

    def move(self, rel: str, folder: str) -> str:
        """Move a note to another folder, with the attachments it links to (they live
        in attachments/ next to the note, so its links keep working)."""
        path = self.path(rel)
        directory = self.path(folder)
        if path.parent == directory:
            return rel
        directory.mkdir(parents=True, exist_ok=True)
        target = self._free(directory, path.stem)
        try:
            links = attachment_links(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            links = []
        os.rename(path, target)
        for link in links:
            source = path.parent / link
            if source.is_file() and not (directory / link).exists():
                (directory / ATTACHMENTS).mkdir(exist_ok=True)
                try:
                    os.rename(source, directory / link)
                except OSError as e:
                    log.warning("could not move attachment %s: %s", source, e)
        new = self._rel(target)
        self._repin(rel, new)
        return new

    def attachments_dir(self, rel: str) -> Path:
        """Where images pasted into this note are saved."""
        return self.path(rel).parent / ATTACHMENTS

    def delete(self, rel: str) -> None:
        """Move a note to the Trash."""
        self._trash(self.path(rel))
        self._unpin(rel)

    def discard(self, rel: str) -> None:
        """Remove a note for good (an untitled note left empty: nothing to keep)."""
        self.path(rel).unlink(missing_ok=True)
        self._unpin(rel)

    def _unpin(self, rel: str) -> None:
        pins = self.pins()
        if rel in pins:
            self._write_pins([p for p in pins if p != rel])

    def create_folder(self, parent: str, name: str) -> str:
        name = safe_name(name)
        target = self.path(parent) / name
        if target.exists():
            raise NotesError(f"there is already a folder or note named “{name}”")
        target.mkdir(parents=True)
        return self._rel(target)

    def rename_folder(self, rel: str, name: str) -> str:
        if not rel:
            raise NotesError("the notes folder itself can't be renamed here")
        path = self.path(rel)
        target = path.parent / safe_name(name)
        if target == path:
            return rel
        if target.exists():
            raise NotesError(f"there is already a folder named “{target.name}”")
        os.rename(path, target)
        new = self._rel(target)
        self._repin(rel, new, folder=True)
        return new

    def delete_folder(self, rel: str) -> None:
        if not rel:
            raise NotesError("the notes folder itself can't be deleted")
        self._trash(self.path(rel))
        self._write_pins([p for p in self.pins() if not p.startswith(rel + "/")])

    # --- helpers -----------------------------------------------------------------------------

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    @staticmethod
    def _free(directory: Path, name: str, exclude: Path | None = None) -> Path:
        """directory/name.md, or name 2.md, name 3.md... if taken (case-insensitively).
        `exclude` is the file being renamed: its own name doesn't count as taken."""
        taken = (
            {p.name.casefold() for p in directory.iterdir() if p != exclude}
            if directory.exists()
            else set()
        )
        candidate, n = f"{name}.md", 1
        while candidate.casefold() in taken:
            n += 1
            candidate = f"{name} {n}.md"
        return directory / candidate


def flatten(folder: Folder) -> list[Note]:
    notes = list(folder.notes)
    for sub in folder.folders:
        notes.extend(flatten(sub))
    return notes


class NoteSession:
    """The note open in the editor: what was last saved, and how saving should go.

    - save() writes only when the text differs from the last save, and renames the
      file when the title differs from the one it was opened (or last renamed) with.
    - external_change() tells our own writes apart from another app's.
    - A note created empty in this session is discarded if it is left empty."""

    def __init__(self, store: NotesStore, rel: str, *, created_empty: bool = False) -> None:
        self.store = store
        self.rel = rel
        self.created_empty = created_empty
        self._take(store.read(rel))

    def _take(self, text: str) -> None:
        self.saved_text = text
        self.title = title_of(text)
        self.disk_mtime = self.store.mtime(self.rel)

    def is_dirty(self, text: str) -> bool:
        return text != self.saved_text

    def save(self, text: str) -> bool:
        """Save if needed; True if anything was written or renamed."""
        if text == self.saved_text and self.disk_mtime is not None:
            return False
        rename = title_of(text) != self.title and not (self.created_empty and not text.strip())
        self.rel = self.store.save(self.rel, text, rename=rename)
        self.saved_text = text
        if rename:
            self.title = title_of(text)
        self.disk_mtime = self.store.mtime(self.rel)
        return True

    def external_change(self) -> str:
        """ "none", "changed" (another app wrote different text) or "gone"."""
        mtime = self.store.mtime(self.rel)
        if mtime is None:
            self.disk_mtime = None  # the next save writes the file again
            return "gone"
        if mtime == self.disk_mtime:
            return "none"
        try:
            text = self.store.read(self.rel)
        except NotesError:
            return "gone"
        self.disk_mtime = mtime
        return "none" if text == self.saved_text else "changed"

    def reload(self) -> str:
        """Take the text on disk (after another app changed it)."""
        text = self.store.read(self.rel)
        self._take(text)
        return text

    def close(self, text: str) -> bool:
        """Leaving the note: discard it if it was created empty and is still empty.
        Returns True if it was discarded (the caller has already saved otherwise)."""
        if self.created_empty and not text.strip():
            self.store.discard(self.rel)
            return True
        return False


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _delete_for_good(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
