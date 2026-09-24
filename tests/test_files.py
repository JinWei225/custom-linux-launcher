import os
import random
import string

import pytest

from launcher.config import ConfigError, FilesConfig, parse_config
from launcher.files_index import FileIndex, IndexSettings, ago, display_dir, scan
from launcher.providers.files import FilesProvider
from launcher.ranking import fuzzy_score

NOW = 2_000_000_000.0
DAY = 86400.0


def touch(path, age_days=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    t = NOW - age_days * DAY
    os.utime(path, (t, t))
    return path


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "Documents"
    touch(root / "report-2024.pdf", age_days=30)
    touch(root / "report-draft.pdf", age_days=1)
    touch(root / "notes" / "meeting notes.txt")
    touch(root / "notes" / "deep" / "deeper" / "bottom.txt")
    touch(root / ".hidden-secret.txt")
    touch(root / "project" / "node_modules" / "left-pad.js")
    touch(root / "project" / "main.py")
    touch(root / "big.iso.part")
    return root


def build(root, **kwargs):
    settings = IndexSettings(
        roots=(str(root),),
        exclude=kwargs.pop("exclude", ("node_modules", "*.part")),
        **kwargs,
    )
    index = FileIndex()
    index.install(settings, scan(settings))
    return index


def names(results):
    return [entry.name for entry, _score in results]


def test_scan_respects_hidden_excludes_and_depth(tree):
    index = build(tree, max_depth=2)
    all_names = {e.name for e, _ in index.search("", 100)} | set(names(index.search("e", 100)))
    assert "meeting notes.txt" in all_names
    assert ".hidden-secret.txt" not in all_names
    assert "left-pad.js" not in all_names and "big.iso.part" not in all_names
    assert "deep" in all_names  # notes/deep: 2 levels below the folder
    assert "deeper" not in all_names and "bottom.txt" not in all_names


def test_show_hidden(tree):
    assert ".hidden-secret.txt" in names(build(tree, show_hidden=True).search("secret", 10))


def test_symlinked_dirs_are_not_followed(tree, tmp_path):
    outside = tmp_path / "outside"
    touch(outside / "elsewhere.txt")
    os.symlink(outside, tree / "link")
    os.symlink(tree, tree / "loop")  # would recurse forever if followed
    index = build(tree)
    assert "elsewhere.txt" not in names(index.search("elsewhere", 10))
    assert "link" in names(index.search("link", 10))


def test_recent_file_ranks_above_older_same_match(tree):
    assert names(build(tree).search("report", 10, now=NOW))[:2] == [
        "report-draft.pdf",
        "report-2024.pdf",
    ]


def test_empty_query_lists_recent_files_only(tree):
    results = names(build(tree).search("", 3, now=NOW))
    assert results[0] in {"meeting notes.txt", "main.py", "bottom.txt"}
    assert "notes" not in results  # folders are not "recent files"


def test_path_query(tree):
    assert names(build(tree).search("notes/meet", 5))[0] == "meeting notes.txt"


def test_prefilter_never_drops_a_real_match(tmp_path):
    rng = random.Random(7)
    root = tmp_path / "r"
    for _ in range(300):
        name = "".join(
            rng.choice(string.ascii_lowercase + " -._") for _ in range(rng.randint(3, 20))
        )
        touch(root / (name.strip() or "x"))
    index = build(root, exclude=())
    entries = [e for e, _ in index.search("", 10_000)]
    for query in ("ab", "e.t", "x-y", "qz", "a b", "."):
        expected = {e.name for e in entries if fuzzy_score(query, e.name) is not None}
        got = set(names(index.search(query, 10_000)))
        assert got == expected, query


def test_rescan_dir_adds_removes_and_reports_dirs(tree):
    index = build(tree)
    touch(tree / "new.txt")
    (tree / "report-2024.pdf").unlink()
    touch(tree / "fresh" / "inside.txt")
    added, removed = index.rescan_dir(str(tree))
    assert str(tree / "fresh") in added and removed == []
    assert "new.txt" in names(index.search("new", 5))
    assert "inside.txt" in names(index.search("inside", 5))
    assert "report-2024.pdf" not in names(index.search("report", 5))

    for p in (tree / "notes").rglob("*"):
        if p.is_file():
            p.unlink()
    for p in sorted((tree / "notes").rglob("*"), reverse=True):
        p.rmdir()
    (tree / "notes").rmdir()
    added, removed = index.rescan_dir(str(tree))
    assert str(tree / "notes") in removed and str(tree / "notes" / "deep") in removed
    assert index.search("meeting", 5) == []


def test_rescan_unknown_dir_is_ignored(tree):
    assert build(tree).rescan_dir("/nonexistent") == ([], [])


def test_scan_stops_at_limit(tree):
    settings = IndexSettings(roots=(str(tree),))
    state = scan(settings, max_entries=3)
    assert len(state.entries) == 3 and state.truncated


def test_missing_root_is_skipped(tmp_path, caplog):
    state = scan(IndexSettings(roots=(str(tmp_path / "nope"),)))
    assert state.entries == {} and "does not exist" in caplog.text


def test_settings_from_config_expands_and_dedupes(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir("/")
    settings = IndexSettings.from_config(
        FilesConfig(folders=("~/Downloads", "~/Downloads/", "Desktop", "/srv/share"))
    )
    assert settings.roots == (
        str(tmp_path / "Downloads"),
        str(tmp_path / "Desktop"),  # relative means relative to home, not the cwd
        "/srv/share",
    )


def test_files_config_lists_are_validated():
    config, _ = parse_config({"files": {"folders": ["~/A"], "exclude": []}})
    assert config.files.folders == ("~/A",) and config.files.exclude == ()
    with pytest.raises(ConfigError, match=r"files.folders\[2\]"):
        parse_config({"files": {"folders": ["~/A", 3]}})
    with pytest.raises(ConfigError, match="must be a list"):
        parse_config({"files": {"folders": "~/A"}})


def test_display_helpers():
    assert display_dir("/home/me/Downloads/x", home="/home/me") == "~/Downloads/x"
    assert display_dir("/home/me", home="/home/me") == "~"
    assert display_dir("/home/meow", home="/home/me") == "/home/meow"
    assert ago(NOW - 30, NOW) == "just now"
    assert ago(NOW - 5 * 3600, NOW) == "5h ago"
    assert ago(NOW - 3 * DAY, NOW) == "3d ago"


def test_provider_actions(tree, host):
    provider = FilesProvider(host, build(tree), icon=lambda e: "icon")
    [result] = [r for r in provider.query("meeting") if r.title == "meeting notes.txt"]
    assert "notes" in result.subtitle
    result.action()
    result.alt_action()
    path = str(tree / "notes" / "meeting notes.txt")
    assert host.calls == [("open-file", path), ("reveal", path)]
