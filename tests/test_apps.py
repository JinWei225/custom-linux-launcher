from launcher.config import Config
from launcher.providers.apps import AppEntry, AppsProvider
from launcher.providers.base import ALIAS_SCORE

APPS = [
    AppEntry("code.desktop", "Visual Studio Code", "Text Editor", executable="code"),
    AppEntry("org.gnome.Nautilus.desktop", "Files", "File Manager", keywords=("folder",)),
    AppEntry("firefox_firefox.desktop", "Firefox", "Web Browser", executable="firefox"),
    AppEntry("org.gnome.Settings.desktop", "Settings", keywords=("preferences", "wifi")),
]


def provider(host, apps=APPS, aliases=None):
    p = AppsProvider(host, loader=lambda: list(apps))
    p.configure(Config(aliases=aliases or {}))
    return p


def ids(results):
    return [r.id for r in sorted(results, key=lambda r: -r.score)]


def test_name_match_ranks_first(host):
    assert ids(provider(host).query("fire"))[0] == "app:firefox_firefox.desktop"


def test_initials_and_executable_match(host):
    assert ids(provider(host).query("vsc"))[0] == "app:code.desktop"
    assert ids(provider(host).query("code"))[0] == "app:code.desktop"


def test_keywords_need_a_real_substring(host):
    assert "app:org.gnome.Settings.desktop" in ids(provider(host).query("wifi"))
    # "wf" is only a scattered match inside the keyword "wifi": ignored for keywords
    assert "app:org.gnome.Settings.desktop" not in ids(provider(host).query("wf"))


def test_alias_by_id_and_by_name(host):
    p = provider(host, aliases={"vs": "code", "fm": "files"})
    top = sorted(p.query("vs"), key=lambda r: -r.score)[0]
    assert (top.id, top.score) == ("app:code.desktop", ALIAS_SCORE)
    assert "vs →" in top.subtitle
    assert ids(p.query("FM"))[0] == "app:org.gnome.Nautilus.desktop"


def test_unknown_alias_target_is_ignored(host, caplog):
    p = provider(host, aliases={"x": "Not Installed"})
    p.query("x")
    assert "no installed app matches" in caplog.text


def test_empty_query_lists_all_apps(host):
    assert len(provider(host).query("")) == len(APPS)


def test_action_launches_by_desktop_id(host):
    provider(host).query("firefox")[0].action()
    assert host.calls == [("launch", "firefox_firefox.desktop")]


def test_invalidate_reloads_and_reresolves_aliases(host):
    apps = list(APPS)
    p = provider(host, apps=apps, aliases={"ed": "Gedit"})
    assert all(r.score < ALIAS_SCORE for r in p.query("ed"))
    apps.append(AppEntry("org.gnome.gedit.desktop", "Gedit"))
    p.invalidate()
    assert sorted(p.query("ed"), key=lambda r: -r.score)[0].score == ALIAS_SCORE
