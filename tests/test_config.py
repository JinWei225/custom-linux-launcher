import tomllib

import pytest

from launcher.config import (
    DEFAULT_CONFIG_TEXT,
    AppSettings,
    Config,
    ConfigError,
    QuickLink,
    UIConfig,
    ensure_config,
    load_config,
    parse_config,
)


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "config.toml") == (Config(), [])


def test_default_text_parses_cleanly():
    config, warnings = parse_config(tomllib.loads(DEFAULT_CONFIG_TEXT))
    assert warnings == []
    assert config.ui == UIConfig()
    assert [link.alias for link in config.quicklinks] == ["g"]


def test_ensure_config_writes_once(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    ensure_config(path)
    assert path.read_text() == DEFAULT_CONFIG_TEXT
    path.write_text("[ui]\nwidth = 900\n")
    ensure_config(path)
    assert load_config(path)[0].ui.width == 900


def test_partial_override_keeps_other_defaults():
    config, _ = parse_config({"ui": {"max_results": 5}})
    assert config.ui == UIConfig(max_results=5)


def test_unknown_keys_warn():
    _, warnings = parse_config({"ui": {"colour": "red"}, "extra": 1})
    assert "unknown setting 'ui.colour' ignored" in warnings
    assert "unknown setting 'extra' ignored" in warnings


@pytest.mark.parametrize(
    "ui",
    [
        {"width": "wide"},
        {"width": True},  # bool must not pass as int
        {"hide_on_focus_loss": 1},  # int must not pass as bool
        {"width": 100},  # out of range
        {"max_results": 50},
    ],
)
def test_invalid_values_raise(ui):
    with pytest.raises(ConfigError):
        parse_config({"ui": ui})


def test_section_must_be_table():
    with pytest.raises(ConfigError):
        parse_config({"ui": 3})


def test_bad_toml_raises(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[ui\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_quicklinks_and_aliases():
    config, warnings = parse_config(
        {
            "apps": {"code.desktop": {"alias": "code", "hotkey": "<Super><Shift>c"}},
            "quicklink": [
                {"name": "GitHub", "alias": "gh", "url": "https://github.com/"},
                {"name": "G", "url": "https://g.com/?q={query}", "fallback": True},
            ],
        }
    )
    assert warnings == []
    assert config.apps == {"code.desktop": AppSettings("code", "<Super><Shift>c")}
    assert config.quicklinks == (
        QuickLink(name="GitHub", url="https://github.com/", alias="gh"),
        QuickLink(name="G", url="https://g.com/?q={query}", fallback=True),
    )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"quicklink": [{"name": "x"}]}, "missing required setting 'url'"),
        ({"quicklink": [{"name": "x", "url": "github.com"}]}, "needs a scheme"),
        ({"quicklink": [{"name": "x", "url": "https://a", "fallback": True}]}, "{query}"),
        ({"quicklink": [{"name": "x", "url": "https://a", "alias": "a b"}]}, "spaces"),
        ({"quicklink": {"name": "x"}}, "list of tables"),
        ({"apps": {"code.desktop": {"alias": 1}}}, 'apps."code.desktop".alias'),
        ({"aliases": {"code": "code.desktop"}}, "replaced by per-app tables"),
    ],
)
def test_invalid_links_raise(data, message):
    with pytest.raises(ConfigError, match=message.replace("{", "\\{").replace("}", "\\}")):
        parse_config(data)


def test_duplicate_alias_is_an_error_case_insensitively():
    with pytest.raises(ConfigError, match="'gh' is used by both"):
        parse_config(
            {
                "apps": {"github-desktop.desktop": {"alias": "GH"}},
                "quicklink": [{"name": "GitHub", "alias": "gh", "url": "https://github.com/"}],
            }
        )


def test_empty_legacy_aliases_table_is_ignored():
    assert parse_config({"aliases": {}}) == (Config(), [])


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"shortcuts": {"files": "f"}}, "needs Super, Ctrl or Alt"),
        ({"shortcuts": {"files": "<Super><Bogus>f"}}, "not a shortcut"),
        (
            {
                "shortcuts": {"files": "<Shift><Super>F"},
                "apps": {"a": {"hotkey": "<super><shift>f"}},
            },
            "is used by both the launcher's files shortcut and app 'a'",
        ),
        (
            {
                "quicklink": [
                    {"name": "A", "url": "https://a"},
                    {"name": "a", "url": "https://b"},
                ]
            },
            "same name",
        ),
    ],
)
def test_hotkey_and_name_rules(data, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_unknown_quicklink_key_warns():
    _, warnings = parse_config({"quicklink": [{"name": "x", "url": "https://a", "kw": "x"}]})
    assert warnings == ["unknown setting 'quicklink[1].kw' ignored"]
