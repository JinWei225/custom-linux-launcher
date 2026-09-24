import shlex

from launcher.config import AppSettings, Config, QuickLink, ShortcutsConfig
from launcher.shortcuts import OWNED_PREFIX, _is_owned, _runs_launcher, desired_bindings, slug


def test_desired_bindings_cover_modes_apps_and_links():
    config = Config(
        shortcuts=ShortcutsConfig(
            launcher="<Control>space", files="<Super><Shift>f", clipboard="", clipboard_pause=""
        ),
        apps={
            "code.desktop": AppSettings("code", "<Super><Shift>c"),
            "x.desktop": AppSettings("x"),
        },
        quicklinks=(
            QuickLink("Git Hub", "https://github.com", hotkey="<Super><Shift>g"),
            QuickLink("No Key", "https://a.b"),
        ),
    )
    bindings = {b.key: b for b in desired_bindings(config, "/home/u/.local/bin/launcher")}
    assert bindings["launcher-main"].command == "/home/u/.local/bin/launcher"
    assert bindings["launcher-files"].command.endswith("--mode files")
    assert "launcher-clipboard" not in bindings  # unbound modes are not registered
    app = next(b for k, b in bindings.items() if k.startswith(OWNED_PREFIX + "app-"))
    assert shlex.split(app.command)[-2:] == ["--run", "app:code.desktop"]
    link = next(b for k, b in bindings.items() if k.startswith(OWNED_PREFIX + "link-"))
    assert shlex.split(link.command)[-1] == "quicklink:Git Hub"  # quoted: name has a space
    assert len(bindings) == 4


def test_slug_is_dconf_safe_and_distinct():
    assert slug("Git Hub!").startswith("git-hub-")
    assert slug("a b") != slug("a-b")
    assert all(c.isalnum() or c == "-" for c in slug("Ünïcode / weird:name"))


def test_owned_and_runs_launcher():
    base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
    assert _is_owned(base + "launcher-files/") and not _is_owned(base + "custom0/")
    assert _runs_launcher("/home/u/.local/bin/launcher --mode files", "/other/launcher")
    assert _runs_launcher("launcher", "/x/launcher")
    assert not _runs_launcher("ulauncher-toggle", "/x/launcher")
    assert not _runs_launcher("unbalanced 'quote", "/x/launcher")
