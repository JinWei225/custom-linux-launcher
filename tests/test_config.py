import tomllib

import pytest

from launcher.config import (
    DEFAULT_CONFIG_TEXT,
    Config,
    ConfigError,
    UIConfig,
    ensure_config,
    load_config,
    parse_config,
)


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "config.toml") == (Config(), [])


def test_default_text_parses_to_defaults():
    config, warnings = parse_config(tomllib.loads(DEFAULT_CONFIG_TEXT))
    assert config == Config()
    assert warnings == []


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
