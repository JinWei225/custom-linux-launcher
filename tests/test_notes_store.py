import json
import os

import pytest

from launcher.config import ConfigError, parse_texts
from launcher.notes_store import (
    NotesError,
    NoteSession,
    NotesStore,
    flatten,
    safe_name,
    title_of,
)


@pytest.fixture
def trashed():
    return []


@pytest.fixture
def store(tmp_path, trashed):
    def trash(path):
        trashed.append(path.name)
        if path.is_dir():
            import shutil

            shutil.rmtree(path)
        else:
            path.unlink()

    s = NotesStore(tmp_path / "Notes", trash=trash)
    s.ensure()
    return s


def write(store, rel, text):
    path = store.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- titles and names ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text, title",
    [
        ("# Lecture 3\nbody", "Lecture 3"),
        ("## Week 2  \n", "Week 2"),
        ("\n\n  plain first line\nsecond", "plain first line"),
        ("- [ ] todo item", "todo item"),
        ("1. first", "first"),
        ("", "Untitled"),
        ("   \n\n", "Untitled"),
        ("#", "Untitled"),  # a heading being typed: no title yet
        ("#hashtag", "#hashtag"),
        ("# ", "Untitled"),
        ("x" * 200, "x" * 80),
    ],
)
def test_title_of(text, title):
    assert title_of(text) == title


@pytest.mark.parametrize(
    "title, name",
    [
        ("Lecture 3", "Lecture 3"),
        ("Signals / Systems", "Signals - Systems"),
        ('a:b*c?"d<e>f|g\\h', "a-b-c-d-e-f-g-h"),
        (".hidden", "hidden"),
        ("   ", "Untitled"),
        ("tab\there", "tab-here"),
        ("中文笔记 한국어", "中文笔记 한국어"),
    ],
)
def test_safe_name(title, name):
    assert safe_name(title) == name


# --- tree ------------------------------------------------------------------------------


def test_tree_lists_markdown_notes_and_folders(store):
    write(store, "b.md", "# Beta")
    write(store, "a.md", "# alpha")
    write(store, "FYP/Week 1.md", "# Week 1")
    write(store, "FYP/Sub/deep.md", "deep")
    write(store, "Courses/x.md", "x")
    write(store, "picture.png", "not a note")
    write(store, ".hidden.md", "hidden")
    write(store, ".git/config", "x")
    write(store, "FYP/attachments/shot.png", "img")
    tree = store.tree()
    assert [n.title for n in tree.notes] == ["alpha", "Beta"]  # by title, case-insensitive
    assert [f.name for f in tree.folders] == ["Courses", "FYP"]
    fyp = tree.folders[1]
    assert fyp.rel == "FYP" and [f.rel for f in fyp.folders] == ["FYP/Sub"]
    assert [n.rel for n in fyp.notes] == ["FYP/Week 1.md"]
    assert fyp.notes[0].folder == "FYP" and tree.notes[0].folder == ""
    assert sorted(n.rel for n in flatten(tree)) == [
        "Courses/x.md", "FYP/Sub/deep.md", "FYP/Week 1.md", "a.md", "b.md",
    ]  # fmt: skip
    assert store.folders(tree) == ["", "Courses", "FYP", "FYP/Sub"]


def test_recent_is_newest_first(store):
    for i, name in enumerate(["old", "mid", "new"]):
        path = write(store, f"{name}.md", name)
        os.utime(path, (1000 + i, 1000 + i))
    assert [n.title for n in store.recent()] == ["new", "mid", "old"]
    assert [n.title for n in store.recent(limit=2)] == ["new", "mid"]


def test_paths_cannot_escape_the_notes_folder(store):
    with pytest.raises(NotesError):
        store.read("../outside.md")
    with pytest.raises(NotesError):
        store.create("../..")


# --- creating and saving ---------------------------------------------------------------


def test_create_names_after_title_and_avoids_clashes(store):
    assert store.create().rel == "Untitled.md"
    assert store.create().rel == "Untitled 2.md"
    assert store.create("FYP", "# Plan\n").rel == "FYP/Plan.md"
    write(store, "plan.md", "")
    assert store.create("", "# Plan").rel == "Plan 2.md"  # clash is case-insensitive
    assert store.read("FYP/Plan.md") == "# Plan\n"


def test_save_writes_only_changes(store):
    note = store.create(text="hello")
    path = store.path(note.rel)
    os.utime(path, (1000, 1000))
    assert store.save(note.rel, "hello") == note.rel
    assert path.stat().st_mtime == 1000  # untouched
    store.save(note.rel, "hello again")
    assert path.read_text() == "hello again"
    assert not any(p.name.endswith(".tmp") for p in store.root.iterdir())


def test_save_with_rename_follows_the_title(store):
    rel = store.create().rel
    rel = store.save(rel, "# Lecture 3\n", rename=True)
    assert rel == "Lecture 3.md"
    assert store.read(rel) == "# Lecture 3\n"
    assert not (store.root / "Untitled.md").exists()
    write(store, "Taken.md", "# Taken")
    rel = store.save(rel, "# Taken\n", rename=True)
    assert rel == "Taken 2.md"


def test_case_only_rename_keeps_the_name(store):
    rel = store.create(text="# lecture").rel
    assert store.save(rel, "# Lecture", rename=True) == "Lecture.md"


def test_rename_note_rewrites_first_line(store):
    rel = store.create(text="## Old title\n\nbody\n").rel
    rel = store.rename_note(rel, "New  title")
    assert rel == "New title.md"
    assert store.read(rel) == "## New title\n\nbody\n"
    plain = store.create(text="first line\nsecond").rel
    assert store.read(store.rename_note(plain, "Renamed")) == "Renamed\nsecond"
    empty = store.create().rel
    assert store.read(store.rename_note(empty, "Given")) == "# Given\n"
    with pytest.raises(NotesError):
        store.rename_note(empty, "   ")


def test_move_note(store):
    rel = store.create(text="# A").rel
    store.create_folder("", "FYP")
    assert store.move(rel, "FYP") == "FYP/A.md"
    assert store.move("FYP/A.md", "FYP") == "FYP/A.md"  # already there
    write(store, "A.md", "# A")
    assert store.move("FYP/A.md", "") == "A 2.md"  # a note with the name is there


def test_delete_goes_to_trash(store, trashed):
    rel = store.create(text="# Gone").rel
    store.set_pinned(rel, True)
    store.delete(rel)
    assert trashed == ["Gone.md"]
    assert store.pins() == []


def test_discard_removes_for_good(store, trashed):
    rel = store.create().rel
    store.discard(rel)
    assert trashed == [] and not (store.root / rel).exists()


# --- folders --------------------------------------------------------------------------


def test_folder_operations(store, trashed):
    assert store.create_folder("", "Courses") == "Courses"
    assert store.create_folder("Courses", "Signals / Systems") == "Courses/Signals - Systems"
    with pytest.raises(NotesError):
        store.create_folder("", "Courses")
    note = store.create("Courses", "# N").rel
    store.set_pinned(note, True)
    assert store.rename_folder("Courses", "Uni") == "Uni"
    assert store.pins() == ["Uni/N.md"]  # pins follow the folder
    with pytest.raises(NotesError):
        store.rename_folder("", "x")
    store.create_folder("", "Other")
    with pytest.raises(NotesError):
        store.rename_folder("Uni", "Other")
    store.delete_folder("Uni")
    assert trashed == ["Uni"] and store.pins() == []
    with pytest.raises(NotesError):
        store.delete_folder("")


# --- pins -----------------------------------------------------------------------------


def test_pins(store):
    a = store.create(text="# A").rel
    b = store.create(text="# B").rel
    store.set_pinned(b, True)
    store.set_pinned(a, True)
    store.set_pinned(a, True)  # no duplicates
    assert [n.title for n in store.pinned()] == ["B", "A"]
    store.set_pinned(b, False)
    assert store.pins() == [a]
    data = json.loads((store.root / ".notes.json").read_text())
    assert data == {"pinned": [a]}


def test_pins_follow_renames_and_skip_missing(store):
    rel = store.create(text="# A").rel
    store.set_pinned(rel, True)
    rel = store.save(rel, "# Renamed", rename=True)
    assert store.pins() == ["Renamed.md"]
    (store.root / "Renamed.md").unlink()
    assert store.pinned() == []


def test_broken_pins_file_is_ignored(store):
    (store.root / ".notes.json").write_text("not json")
    assert store.pins() == []
    (store.root / ".notes.json").write_text('{"pinned": [1, "a.md"]}')
    assert store.pins() == ["a.md"]


# --- the open note ---------------------------------------------------------------------


def test_session_saves_only_changes_and_renames_on_title_edit(store):
    rel = store.create().rel
    session = NoteSession(store, rel, created_empty=True)
    assert session.title == "Untitled"
    assert not session.save("")  # nothing changed
    assert session.save("# Lecture")
    assert session.rel == "Lecture.md" and session.title == "Lecture"
    assert session.save("# Lecture\n\nbody")  # same title: same file
    assert session.rel == "Lecture.md"
    assert not session.is_dirty("# Lecture\n\nbody")


def test_session_does_not_rename_other_apps_files_unless_title_changes(store):
    write(store, "my-file-name.md", "# Some title\n")
    session = NoteSession(store, "my-file-name.md")
    session.save("# Some title\n\nmore text")
    assert session.rel == "my-file-name.md"
    session.save("# Better title\n\nmore text")
    assert session.rel == "Better title.md"


def test_session_tells_own_writes_from_other_apps(store):
    rel = store.create(text="# mine").rel
    session = NoteSession(store, rel)
    session.save("# mine\nedited")
    assert session.external_change() == "none"  # our own write
    path = store.path(session.rel)
    path.write_text("theirs")
    os.utime(path, (5000, 5000))
    assert session.external_change() == "changed"
    assert session.external_change() == "none"  # reported once
    assert session.reload() == "theirs" and session.saved_text == "theirs"
    os.utime(path, (6000, 6000))  # touched but same text
    assert session.external_change() == "none"
    path.unlink()
    assert session.external_change() == "gone"
    assert session.save("theirs")  # same text, but the file is written again
    assert path.read_text() == "theirs"


def test_session_discards_new_note_left_empty(store):
    rel = store.create().rel
    session = NoteSession(store, rel, created_empty=True)
    assert session.close("  \n")
    assert not (store.root / rel).exists()
    rel = store.create().rel
    session = NoteSession(store, rel, created_empty=True)
    session.save("kept")
    assert not session.close("kept")
    existing = NoteSession(store, session.rel)  # opened, not created: never discarded
    assert not existing.close("")


# --- config ---------------------------------------------------------------------------


def test_notes_config():
    config, _ = parse_texts("", None)
    assert config.notes.folder == "~/Notes"
    assert config.shortcuts.notes == "<Super><Shift>n"
    config, _ = parse_texts('[notes]\nfolder = "~/Documents/Notes"', None)
    assert config.notes.folder == "~/Documents/Notes"
    with pytest.raises(ConfigError):
        parse_texts('[notes]\nfolder = " "', None)
