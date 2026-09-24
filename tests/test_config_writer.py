import pytest

from launcher.config import AppSettings, ConfigError, QuickLink, load_config
from launcher.config_writer import ConfigWriter

START = """\
# my comment stays
[ui]
width = 680  # inline comment stays

[[quicklink]]
name = "GitHub"
alias = "gh"
url = "https://github.com/"

[[quicklink]]
name = "Google"
alias = "g"
url = "https://google.com/?q={query}"
fallback = true
"""


@pytest.fixture
def writer(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(START)
    return ConfigWriter(path)


def test_set_value_keeps_comments(writer):
    config = writer.set_value("ui", "width", 900)
    text = writer.path.read_text()
    assert config.ui.width == 900
    assert "# my comment stays" in text and "# inline comment stays" in text


def test_invalid_edit_writes_nothing(writer):
    with pytest.raises(ConfigError):
        writer.set_value("ui", "width", 5)
    with pytest.raises(ConfigError):
        writer.save_quicklink("GitHub", QuickLink("GitHub", "github.com"))  # no scheme
    with pytest.raises(ConfigError, match="used by both"):
        writer.set_app("x.desktop", AppSettings(alias="gh"))
    assert writer.path.read_text() == START
    assert not writer.path.with_name("config.toml.tmp").exists()


def test_app_settings_added_updated_and_removed(writer):
    writer.set_app("code.desktop", AppSettings("code", "<Super><Shift>c"))
    assert load_config(writer.path)[0].apps["code.desktop"] == AppSettings(
        "code", "<Super><Shift>c"
    )
    writer.set_app("code.desktop", AppSettings("vsc", ""))
    text = writer.path.read_text()
    assert 'alias = "vsc"' in text and "hotkey" not in text
    writer.set_app("code.desktop", AppSettings())
    assert writer.path.read_text() == START  # back to exactly the original file


def test_quicklink_edit_add_delete_move(writer):
    writer.save_quicklink(
        "GitHub", QuickLink("GitHub", "https://github.com/", "gh", hotkey="<Super>g")
    )
    writer.save_quicklink(None, QuickLink("Wiki", "https://w.org/{query}", "w", fallback=True))
    names = [link.name for link in load_config(writer.path)[0].quicklinks]
    assert names == ["GitHub", "Google", "Wiki"]
    writer.move_quicklink("Wiki", -1)
    writer.move_quicklink("GitHub", +5)  # clamps at the end
    config = writer.delete_quicklink("google")  # case-insensitive
    assert [link.name for link in config.quicklinks] == ["Wiki", "GitHub"]
    assert config.quicklinks[1].hotkey == "<Super>g"


def test_optional_fields_are_dropped_when_cleared(writer):
    writer.save_quicklink("Google", QuickLink("Google", "https://google.com/?q={query}", "g"))
    text = writer.path.read_text()
    assert "fallback" not in text


def test_edit_rereads_file_so_hand_edits_survive(writer):
    writer.path.write_text(writer.path.read_text().replace("width = 680", "width = 700"))
    writer.set_value("ui", "max_results", 5)
    config = load_config(writer.path)[0]
    assert (config.ui.width, config.ui.max_results) == (700, 5)


def test_missing_quicklink_is_reported(writer):
    with pytest.raises(ConfigError, match="no longer exists"):
        writer.delete_quicklink("Nope")


def test_lists_are_written_as_arrays(writer):
    config = writer.set_value("files", "folders", ["~/A", "~/B"])
    assert config.files.folders == ("~/A", "~/B")


def test_new_keys_go_after_the_last_key_not_after_trailing_comments(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[shortcuts]\n"
        'launcher = "<Control>space"\n'
        "\n"
        "# Section about quicklinks\n"
        "[[quicklink]]\n"
        'name = "A"\n'
        'url = "https://a"\n'
        "\n"
        "# Another comment\n"
        "[[quicklink]]\n"
        'name = "B"\n'
        'url = "https://b"\n'
    )
    writer = ConfigWriter(path)
    writer.set_value("shortcuts", "files", "<Super><Shift>f")
    writer.save_quicklink("A", QuickLink("A", "https://a", hotkey="<Super>a"))
    lines = path.read_text().splitlines()
    assert lines[:3] == ["[shortcuts]", 'launcher = "<Control>space"', 'files = "<Super><Shift>f"']
    a_block = lines[lines.index('name = "A"') : lines.index('name = "A"') + 3]
    assert a_block == ['name = "A"', 'url = "https://a"', 'hotkey = "<Super>a"']
    assert load_config(path)[1] == []  # nothing ended up in the wrong table
