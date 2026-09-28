import os
import time

import pytest

from launcher.config import Config, NotesConfig
from launcher.engine import MODES
from launcher.notes_store import NotesStore
from launcher.providers.notes import NotesProvider


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def notes(tmp_path):
    root = tmp_path / "Notes"
    (root / "Signals").mkdir(parents=True)
    (root / "Lecture 3.md").write_text("# Lecture 3: Fourier Series\n\nnotes")
    (root / "Signals" / "week-2.md").write_text("## Week 2 **labs**\n- a")
    (root / "shopping.md").write_text("- [ ] milk")
    (root / ".hidden.md").write_text("# Secret")
    (root / "attachments").mkdir()
    (root / "attachments" / "x.md").write_text("# Not a note")
    return root


def provider_for(root, host, clock=None):
    provider = NotesProvider(host, clock=clock or Clock())
    provider.configure(Config(notes=NotesConfig(folder=str(root))))
    return provider


def titles(results):
    return [r.title for r in sorted(results, key=lambda r: r.score, reverse=True)]


def test_finds_notes_by_title(notes, host):
    provider = provider_for(notes, host)
    assert titles(provider.query("fourier")) == ["Lecture 3: Fourier Series"]
    assert titles(provider.query("week 2")) == ["Week 2 labs"]
    assert titles(provider.query("milk")) == ["milk"]
    assert provider.query("secret") == []  # hidden files aren't notes
    assert provider.query("not a note") == []  # nor is anything in attachments/
    assert provider.query("") == []  # only for a search


def test_finds_notes_by_file_name(notes, host):
    assert titles(provider_for(notes, host).query("week-2")) == ["Week 2 labs"]


def test_result_opens_the_note_and_alt_reveals_it(notes, host):
    (result,) = provider_for(notes, host).query("fourier")
    assert result.id == "note:Lecture 3.md"
    assert result.subtitle.startswith("Notes · ")
    result.action()
    result.alt_action()
    path = str(notes / "Lecture 3.md")
    assert host.calls == [("note", path), ("reveal", path)]
    (week,) = provider_for(notes, host).query("week 2")
    assert week.subtitle.startswith("Notes/Signals · ")


def test_rescans_at_most_once_a_second(notes, host):
    clock = Clock()
    provider = provider_for(notes, host, clock)
    assert provider.query("physics") == []
    (notes / "Physics.md").write_text("# Physics")
    clock.now += 0.5
    assert provider.query("physics") == []  # the scan from half a second ago
    clock.now += 0.6
    assert titles(provider.query("physics")) == ["Physics"]


def test_renamed_title_is_picked_up(notes, host):
    clock = Clock()
    provider = provider_for(notes, host, clock)
    assert provider.query("fourier")
    path = notes / "Lecture 3.md"
    path.write_text("# Laplace transforms\n")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    clock.now += 2
    assert provider.query("fourier") == []
    assert titles(provider.query("laplace")) == ["Laplace transforms"]


def test_missing_folder_and_reconfigure(tmp_path, notes, host):
    provider = provider_for(tmp_path / "nowhere", host)
    assert provider.query("fourier") == []
    provider.configure(Config(notes=NotesConfig(folder=str(notes))))
    assert titles(provider.query("fourier")) == ["Lecture 3: Fourier Series"]


def test_in_the_main_search_only():
    assert "notes" in MODES["all"]
    assert all("notes" not in providers for mode, providers in MODES.items() if mode != "all")


def test_store_reads_only_changed_notes(notes, monkeypatch):
    store = NotesStore(notes)
    assert len(store.all_notes()) == 3
    opened = []
    real_open = open

    def spy(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", spy)
    time.sleep(0.01)
    (notes / "shopping.md").write_text("- [ ] bread and milk")
    assert {n.title for n in store.all_notes()} == {
        "Lecture 3: Fourier Series", "Week 2 labs", "bread and milk",
    }  # fmt: skip
    assert opened == [str(notes / "shopping.md")]
