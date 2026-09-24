import os
import stat

import pytest

from launcher.clipboard_recorder import ClipboardRecorder, make_thumbnails
from launcher.clipboard_store import (
    MAX_TEXT_BYTES,
    ClipboardStore,
    app_matches,
    choose_mimetype,
    first_line,
    is_secret,
)
from launcher.config import ClipboardConfig
from launcher.providers.clipboard import ClipboardProvider, human_size
from launcher.providers.commands import CommandsProvider

DAY = 86400.0


def make_png(width: int, height: int) -> bytes:
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
    pixbuf.fill(0x3366FFFF)
    return bytes(pixbuf.save_to_bufferv("png", [], [])[1])


PNG_1x1 = make_png(1, 1)


# --- pure helpers ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offer", "expected"),
    [
        (["text/plain;charset=utf-8", "image/png"], ("text", "text/plain;charset=utf-8")),
        (["UTF8_STRING", "STRING"], ("text", "UTF8_STRING")),
        (["image/jpeg", "image/png", "text/html"], ("image", "image/png")),
        (["image/x-exotic"], ("image", "image/x-exotic")),
        (["x-special/gnome-copied-files"], None),
        ([], None),
    ],
)
def test_choose_mimetype(offer, expected):
    assert choose_mimetype(offer) == expected


def test_secret_and_app_matching():
    assert is_secret(["text/plain", "x-kde-passwordManagerHint"])
    assert not is_secret(["text/plain"])
    assert app_matches(("org.keepassxc.KeePassXC",), "", "org.keepassxc.KeePassXC.desktop")
    assert app_matches(("KEEPASSXC",), "keepassxc", "")
    assert not app_matches(("KeePassXC",), "firefox", "firefox.desktop")
    assert not app_matches(("x",), "", "")


def test_first_line_and_sizes():
    assert first_line("\n\n  hello   world \nsecond") == "hello world"
    assert first_line("x" * 300, width=10) == "x" * 9 + "…"
    assert human_size(512) == "512 B" and human_size(2048) == "2.0 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"


# --- store ------------------------------------------------------------------------------


def test_text_dedupe_moves_to_top_and_keeps_source():
    store = ClipboardStore(None)
    a = store.add_text("alpha", "firefox", now=1)
    store.add_text("beta", "code", now=2)
    again = store.add_text("alpha", "", now=3)  # re-copied by the launcher itself
    assert again.id == a.id and again.source == "firefox"
    assert [c.text for c in store.recent(10)] == ["alpha", "beta"]
    assert len(store) == 2


def test_blank_and_huge_text_are_skipped():
    store = ClipboardStore(None)
    assert store.add_text("   \n\t", "x") is None
    assert store.add_text("x" * (MAX_TEXT_BYTES + 1), "x") is None
    assert len(store) == 0


def test_prune_by_count_and_age_keeps_pinned():
    store = ClipboardStore(None)
    for i in range(6):
        store.add_text(f"clip {i}", "x", now=100 * DAY + i)
    old = store.add_text("ancient", "x", now=1 * DAY)
    pinned = store.add_text("keep me", "x", now=0)
    store.set_pinned(pinned.id, True)
    removed = store.prune(max_entries=4, max_days=30, now=101 * DAY)
    texts = {c.text for c in store.recent(100)}
    assert removed == 3
    assert texts == {"clip 5", "clip 4", "clip 3", "clip 2", "keep me"}
    assert store.get(old.id) is None


def test_search_substring_first_line_and_images():
    store = ClipboardStore(None)
    store.add_text("git push origin main", "ptyxis", now=1)
    store.add_text("unrelated\nsecond line mentions push", "x", now=2)
    store.add_image(PNG_1x1, "image/png", "brave", 1, 1, b"i", b"p", now=3)
    assert [c.text for c, _ in store.search("push", 10)][0] == "git push origin main"
    assert len(store.search("push", 10)) == 2
    assert store.search("png", 10)[0][0].kind == "image"
    assert store.search("", 10)[0][0].kind == "image"  # empty query: most recent first


def test_image_files_are_private_and_cleaned_up(tmp_path):
    store = ClipboardStore(tmp_path / "data")
    clip = store.add_image(PNG_1x1, "image/png", "brave", 1, 1, b"icon", b"preview")
    clips = tmp_path / "data" / "clips"
    assert stat.S_IMODE(os.stat(tmp_path / "data").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tmp_path / "data" / "clipboard.db").st_mode) == 0o600
    data_file = clips / f"{clip.digest}.data"
    assert data_file.read_bytes() == PNG_1x1
    assert stat.S_IMODE(os.stat(data_file).st_mode) == 0o600
    assert store.content(clip.id) == ("image/png", PNG_1x1)
    assert store.image_path(clip, "icon").endswith(".icon.png")
    store.delete(clip.id)
    assert list(clips.iterdir()) == []


def test_clear_keeps_pinned():
    store = ClipboardStore(None)
    keep = store.add_text("keep", "x")
    store.set_pinned(keep.id, True)
    store.add_text("drop", "x")
    assert store.clear() == 1
    assert [c.text for c in store.recent(10)] == ["keep"]


def test_persists_across_instances(tmp_path):
    ClipboardStore(tmp_path).add_text("remember me", "x")
    assert ClipboardStore(tmp_path).recent(1)[0].text == "remember me"


# --- thumbnails ----------------------------------------------------------------------


def test_make_thumbnails_scales_down_but_never_up():
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    def size(png):
        loader = GdkPixbuf.PixbufLoader()
        loader.write(png)
        loader.close()
        pixbuf = loader.get_pixbuf()
        return pixbuf.get_width(), pixbuf.get_height()

    width, height, icon, preview = make_thumbnails(make_png(1920, 1080))
    assert (width, height) == (1920, 1080)
    assert size(icon) == (64, 36) and size(preview) == (480, 270)
    assert size(make_thumbnails(PNG_1x1)[2]) == (1, 1)


def test_make_thumbnails_rejects_garbage():
    from gi.repository import GLib

    with pytest.raises((GLib.Error, ValueError)):
        make_thumbnails(b"not an image")


# --- recorder --------------------------------------------------------------------------


class FakeHelper:
    def __init__(self, content):
        self.content = content
        self.callback = None
        self.requested = []

    def subscribe_clipboard(self, callback):
        self.callback = callback

    def get_clipboard(self, mime, callback):
        self.requested.append(mime)
        callback(self.content.get(mime))


def recorder_with(content, config=None):
    helper = FakeHelper(content)
    store = ClipboardStore(None)
    added = []
    recorder = ClipboardRecorder(
        helper, store, lambda: config or ClipboardConfig(), on_added=lambda: added.append(1)
    )
    return helper, store, recorder, added


def test_recorder_records_text_with_source():
    helper, store, _, added = recorder_with({"text/plain;charset=utf-8": b"hello"})
    helper.callback(["text/plain;charset=utf-8"], "firefox", "firefox_firefox.desktop")
    clip = store.recent(1)[0]
    assert (clip.text, clip.source) == ("hello", "firefox_firefox")
    assert added == [1]


@pytest.mark.parametrize(
    ("mimetypes", "wm_class", "config", "paused"),
    [
        (["text/plain", "x-kde-passwordManagerHint"], "keepassxc", None, False),
        (["text/plain"], "org.keepassxc.KeePassXC", None, False),
        (["text/plain"], "firefox", ClipboardConfig(enabled=False), False),
        (["text/plain"], "firefox", None, True),
    ],
)
def test_recorder_privacy_rules(mimetypes, wm_class, config, paused):
    helper, store, recorder, _ = recorder_with({"text/plain": b"secret"}, config)
    recorder.paused = paused
    helper.callback(mimetypes, wm_class, "")
    assert helper.requested == [] and len(store) == 0


def test_recorder_keeps_source_when_launcher_recopies():
    helper, store, _, _ = recorder_with({"text/plain;charset=utf-8": b"hi"})
    helper.callback(["text/plain;charset=utf-8"], "code", "code.desktop")
    helper.callback(["text/plain;charset=utf-8"], "", "io.github.jinwei.Launcher.desktop")
    assert store.recent(1)[0].source == "code"


def test_recorder_skips_oversized_image():
    big = b"\x89PNG" + b"0" * (2 * 1024 * 1024)
    helper, store, _, _ = recorder_with({"image/png": big}, ClipboardConfig(max_image_mb=1))
    helper.callback(["image/png"], "x", "")
    assert len(store) == 0


# --- provider and commands ---------------------------------------------------------------


def test_provider_results_and_actions(host):
    store = ClipboardStore(None)
    first = store.add_text("line one\nline two", "code", now=1)
    store.add_text("newer", "code", now=2)
    results = ClipboardProvider(host, store).query("")
    assert [r.title for r in results] == ["newer", "line one"]
    assert results[0].score > results[1].score and not results[0].learn
    older = results[1]
    assert older.preview == ("text", "line one\nline two")
    older.action()
    older.alt_action()
    older.key_actions["pin"]()
    older.key_actions["delete"]()
    assert host.calls == [
        ("paste", first.id),
        ("copy-clip", first.id),
        ("pin", first.id, True),
        ("delete", first.id),
    ]


def test_pause_command_reflects_state(host):
    provider = CommandsProvider(host)
    pause = next(r for r in provider.query("clipboard") if r.id == "command:clipboard-pause")
    assert pause.title == "Pause Clipboard Recording"
    pause.action()
    resumed = next(r for r in provider.query("clipboard") if r.id == "command:clipboard-pause")
    assert resumed.title == "Resume Clipboard Recording"
    clear = next(r for r in provider.query("clear clipboard") if r.id == "command:clipboard-clear")
    clear.action()
    assert host.calls == [("pause",), ("clear",)]


def test_clear_recent_range_keeps_pinned_and_older():
    store = ClipboardStore(None)
    store.add_text("yesterday", "x", now=1000)
    recent_pinned = store.add_text("recent pinned", "x", now=5000)
    store.set_pinned(recent_pinned.id, True)
    store.add_text("recent", "x", now=5500)
    assert store.clear(since=4000) == 1
    assert {c.text for c in store.recent(10)} == {"yesterday", "recent pinned"}
    assert store.counts() == (2, 1)


def test_read_counts_is_read_only(tmp_path):
    from launcher.clipboard_store import read_counts

    assert read_counts(tmp_path) == (0, 0)  # no database yet: nothing created
    assert not (tmp_path / "clipboard.db").exists()
    store = ClipboardStore(tmp_path)
    store.set_pinned(store.add_text("a", "x").id, True)
    store.add_text("b", "x")
    assert read_counts(tmp_path) == (2, 1)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "all time"), (900, "the last 15 minutes"), (3600, "the last hour"),
     (86400, "the last day"), (7200, "the last 2 hours"), (90, "the last 90 seconds")],
)  # fmt: skip
def test_describe_span(seconds, text):
    from launcher.app import describe_span

    assert describe_span(seconds) == text


def test_pinned_entries_form_their_own_section_on_top(host):
    store = ClipboardStore(None)
    old = store.add_text("old but pinned", "x", now=1)
    store.set_pinned(old.id, True)
    store.add_text("newest", "x", now=3)
    store.add_text("middle", "x", now=2)
    results = ClipboardProvider(host, store).query("")
    assert [(r.title, r.section) for r in results] == [
        ("old but pinned", "Pinned"),
        ("newest", "Recent"),
        ("middle", "Recent"),
    ]
    assert results[0].score > results[1].score > results[2].score
    # Searching keeps the split: pinned matches first.
    assert [r.title for r in ClipboardProvider(host, store).query("e")][0] == "old but pinned"


def test_no_section_headers_without_pinned_entries(host):
    store = ClipboardStore(None)
    store.add_text("a", "x")
    assert ClipboardProvider(host, store).query("")[0].section is None
