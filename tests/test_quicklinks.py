from launcher.config import Config, QuickLink
from launcher.providers.base import ALIAS_SCORE, FALLBACK_SCORE
from launcher.providers.quicklinks import (
    QuickLinksProvider,
    WebSearchProvider,
    fill_url,
    split_alias,
)

LINKS = (
    QuickLink("Google Search", "https://google.com/search?q={query}", alias="g", fallback=True),
    QuickLink("Wikipedia", "https://en.wikipedia.org/wiki/{query}", alias="wiki", fallback=True),
    QuickLink("GitHub", "https://github.com/", alias="gh"),
)


def configured(cls, host):
    p = cls(host)
    p.configure(Config(quicklinks=LINKS))
    return p


def test_fill_url_encodes_for_query_and_path():
    assert fill_url("https://x/?q={query}", "a b&c") == "https://x/?q=a%20b%26c"
    assert fill_url("https://x/wiki/{query}", "C++") == "https://x/wiki/C%2B%2B"
    assert fill_url("https://x/", "ignored") == "https://x/"


def test_split_alias():
    assert split_alias("  g  some cats ") == ("g", "some cats")
    assert split_alias("gh") == ("gh", "")


def test_plain_link_alias(host):
    result = max(configured(QuickLinksProvider, host).query("gh"), key=lambda r: r.score)
    assert (result.title, result.score) == ("GitHub", ALIAS_SCORE)
    result.action()
    result.alt_action()
    assert host.calls == [("open", "https://github.com/"), ("copy", "https://github.com/")]


def test_search_alias_with_query(host):
    results = configured(QuickLinksProvider, host).query("g black holes")
    top = max(results, key=lambda r: r.score)
    assert (top.title, top.score) == ("Google Search: black holes", ALIAS_SCORE)
    top.action()
    assert host.calls == [("open", "https://google.com/search?q=black%20holes")]


def test_search_alias_alone_offers_tab_completion(host):
    top = max(configured(QuickLinksProvider, host).query("wiki"), key=lambda r: r.score)
    assert top.completion == "wiki "
    assert top.score == ALIAS_SCORE


def test_plain_link_alias_with_extra_words_is_not_alias_match(host):
    results = configured(QuickLinksProvider, host).query("gh issues")
    assert all(r.score < ALIAS_SCORE for r in results)


def test_fuzzy_match_on_name(host):
    top = max(configured(QuickLinksProvider, host).query("githu"), key=lambda r: r.score)
    assert top.title == "GitHub"


def test_fallbacks_for_any_text_in_config_order(host):
    results = configured(WebSearchProvider, host).query("black holes")
    assert [r.title for r in results] == [
        "Search Google Search for “black holes”",
        "Search Wikipedia for “black holes”",
    ]
    assert all(r.score == FALLBACK_SCORE and not r.learn for r in results)
    results[1].action()
    assert host.calls == [("open", "https://en.wikipedia.org/wiki/black%20holes")]


def test_no_fallbacks_when_a_search_alias_is_used(host):
    assert configured(WebSearchProvider, host).query("g cats") == []
    assert configured(WebSearchProvider, host).query("") == []
