import tomllib
from datetime import datetime

import pytest
import yaml

from launcher.config import Config, ConfigError, Snippet, parse_texts
from launcher.config_writer import ConfigWriter
from launcher.importers import espanso_to_snippet, import_espanso, read_espanso
from launcher.snippets import ESPANSO_FILE_NAME, espanso_yaml, expand, parse_body, sync_espanso

NOW = datetime(2026, 9, 24, 14, 5)


def test_expand_placeholders_and_cursor():
    text, after = expand("Hi {clipboard},\n{cursor}\n-- {date} {date:%H:%M}", NOW, "Bob")
    assert text == "Hi Bob,\n\n-- 2026-09-24 14:05"
    assert after == len("\n-- 2026-09-24 14:05")


def test_expand_keeps_other_braces_and_only_first_cursor():
    text, after = expand("if (x) {{ y }} {cursor}{cursor}{nope}", NOW)
    assert text == "if (x) {{ y }} {nope}"
    assert after == len("{nope}")


def test_bad_date_format_is_kept_as_text():
    assert parse_body("{date:}")[0].value == "%Y-%m-%d"
    assert expand("{date:%Q}", NOW)[0] in ("%Q", "Q")  # platform strftime differences


def test_espanso_yaml_translates_placeholders():
    snippets = (
        Snippet("Sig", "Best,\n{cursor}J", trigger=";sig"),
        Snippet("Today", "{date} {date:%H:%M} {date}", trigger=";date"),
        Snippet("Paste only", "no trigger"),
        Snippet("Quote", 'He said "{clipboard}"', trigger=";q"),
    )
    data = yaml.safe_load(espanso_yaml(snippets))
    sig, today, quote = data["matches"]
    assert sig == {"trigger": ";sig", "replace": "Best,\n$|$J"}
    assert today["replace"] == "{{date1}} {{date2}} {{date1}}"
    assert [v["params"]["format"] for v in today["vars"]] == ["%Y-%m-%d", "%H:%M"]
    assert quote["replace"] == 'He said "{{clipboard}}"'
    assert quote["vars"] == [{"name": "clipboard", "type": "clipboard"}]


def test_sync_espanso_only_writes_changes(tmp_path):
    snippets = (Snippet("Sig", "Best", trigger=";sig"),)
    assert sync_espanso(snippets, tmp_path) is True
    assert sync_espanso(snippets, tmp_path) is False  # unchanged: espanso not restarted
    assert sync_espanso((Snippet("x", "y"),), tmp_path) is True  # no triggers: removed
    assert not (tmp_path / ESPANSO_FILE_NAME).exists()
    assert sync_espanso((), tmp_path) is False
    assert sync_espanso(snippets, tmp_path / "missing") is False  # espanso not set up


def test_snippets_file_is_validated_with_config():
    config, _ = parse_texts(
        '[apps."a.desktop"]\nalias = "sig"\n', '[[snippet]]\nname = "S"\nbody = "b"\n'
    )
    assert config.snippets == (Snippet("S", "b"),)
    with pytest.raises(ConfigError, match="alias 'sig'"):
        parse_texts('[apps."a.desktop"]\nalias = "sig"\n', SNIP + 'alias = "sig"\n')
    with pytest.raises(ConfigError, match="trigger ';s'"):
        parse_texts("", SNIP + 'trigger = ";s"\n' + SNIP.replace('"S"', '"T"') + 'trigger=";s"')
    with pytest.raises(ConfigError, match="spaces"):
        parse_texts("", SNIP + 'trigger = "a b"\n')
    with pytest.raises(ConfigError, match="empty body"):
        parse_texts("", '[[snippet]]\nname = "S"\nbody = ""\n')
    with pytest.raises(ConfigError, match="snippets.toml"):
        parse_texts("", "[[snippet]\n")


SNIP = '[[snippet]]\nname = "S"\nbody = "b"\n'


def test_writer_round_trips_multiline_bodies(tmp_path):
    writer = ConfigWriter(tmp_path / "config.toml")
    body = '\nLine "one"\n  C:\\path\\ and \'\'\' and """\nend\n'
    writer.save_snippet(None, Snippet("Multi", body, trigger=";m"))
    writer.save_snippet(None, Snippet("One", "single", alias="one"))
    text = writer.snippets_path.read_text()
    assert text.startswith("# Launcher snippets")  # header for a new file
    assert tomllib.loads(text)["snippet"][0]["body"] == body
    config = writer.save_snippet("multi", Snippet("Multi2", "short"))
    assert [s.name for s in config.snippets] == ["Multi2", "One"]
    config = writer.delete_snippet("One")
    assert [s.name for s in config.snippets] == ["Multi2"]
    with pytest.raises(ConfigError):
        writer.save_snippet(None, Snippet("multi2", "dup name"))
    assert writer.load().snippets == (Snippet("Multi2", "short"),)


ESPANSO = """\
# comment
matches:
  - trigger: ":espanso"
    replace: "Hi there!"
  - trigger: ":date"
    replace: "{{mydate}}"
    vars:
      - name: mydate
        type: date
        params:
          format: "%m/%d/%Y"
  - trigger: ":shell"
    replace: "{{output}}"
    vars:
      - name: output
        type: shell
        params:
          cmd: "echo hi"
  - trigger: "1@@"
    replace: "someone@example.com"
  - trigger: ":word"
    replace: "w"
    word: true
  - triggers: [":a", ":b"]
    replace: "two"
  - trigger: ":cur"
    label: "Cursor test"
    replace: "<b>$|$</b>"
"""


def test_espanso_import_converts_simple_matches(tmp_path):
    base = tmp_path / "base.yml"
    base.write_text(ESPANSO)
    result = read_espanso(base, existing=(Snippet("Hi there!", "x", trigger=";other"),))
    assert [(s.name, s.trigger, s.body) for s in result.snippets] == [
        ("Hi there! (2)", ":espanso", "Hi there!"),
        (":date", ":date", "{date:%m/%d/%Y}"),
        ("someone@example.com", "1@@", "someone@example.com"),
        ("Cursor test", ":cur", "<b>{cursor}</b>"),
    ]
    assert [m.get("trigger", m.get("triggers")) for m in result.kept] == [
        ":shell",
        ":word",
        [":a", ":b"],
    ]


def test_espanso_import_skips_existing_triggers_and_unknown_vars():
    assert espanso_to_snippet({"trigger": ":g", "replace": "{{global}}"}) is None
    assert espanso_to_snippet({"trigger": ":g", "replace": ""}) is None
    assert espanso_to_snippet("nonsense") is None


def test_import_espanso_moves_matches(tmp_path):
    base = tmp_path / "espanso" / "base.yml"
    base.parent.mkdir()
    base.write_text(ESPANSO)
    writer = ConfigWriter(tmp_path / "config.toml")
    moved = import_espanso(writer, base)
    assert len(moved) == 4
    assert (tmp_path / "espanso" / "base.yml.bak").read_text() == ESPANSO
    left = yaml.safe_load(base.read_text())["matches"]
    assert [m.get("trigger") for m in left] == [":shell", ":word", None]
    assert len(writer.load().snippets) == 4
    assert import_espanso(writer, base) == []  # nothing left to import
    assert isinstance(writer.load(), Config)
