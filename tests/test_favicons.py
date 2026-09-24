import os

import pytest

from launcher.config import Config, QuickLink
from launcher.favicons import (
    REFRESH_AFTER,
    RETRY_ERROR_AFTER,
    RETRY_MISSING_AFTER,
    FaviconCache,
    FetchError,
    candidate_domains,
    domain_of,
    looks_like_image,
)
from launcher.providers.quicklinks import LINK_ICON, QuickLinksProvider

PNG = b"\x89PNG\r\n\x1a\n" + b"data"


class InlineExecutor:
    """Runs submitted work immediately so tests are deterministic."""

    def submit(self, fn, *args):
        fn(*args)

    def shutdown(self, **_):
        pass


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_cache(tmp_path, fetch, clock=None, updates=None):
    return FaviconCache(
        tmp_path / "favicons",
        fetch=fetch,
        on_update=(lambda: updates.append(1)) if updates is not None else None,
        executor=InlineExecutor(),
        now=clock or Clock(),
    )


def test_helpers():
    assert domain_of("https://GitHub.com/foo?q=1") == "github.com"
    assert domain_of("mailto:x@y.z") is None
    assert candidate_domains("web.whatsapp.com") == ["web.whatsapp.com", "whatsapp.com"]
    assert candidate_domains("claude.ai") == ["claude.ai"]
    assert looks_like_image(PNG) and looks_like_image(b"\xff\xd8\xff\xe0")
    assert not looks_like_image(b"<!DOCTYPE html>")


def test_first_lookup_fetches_then_serves_from_disk(tmp_path):
    calls, updates = [], []
    cache = make_cache(tmp_path, lambda d: calls.append(d) or PNG, updates=updates)
    assert cache.icon_for("https://github.com/") is None  # never blocks on the network
    path = cache.icon_for("https://github.com/x")
    assert path and open(path, "rb").read() == PNG
    assert calls == ["github.com"] and updates == [1]


def test_site_without_icon_is_not_asked_again_until_retry(tmp_path):
    clock, calls = Clock(), []
    cache = make_cache(tmp_path, lambda d: calls.append(d) or None, clock)
    cache.icon_for("https://nothing.test/")
    cache.icon_for("https://nothing.test/")
    assert calls == ["nothing.test"]
    marker = tmp_path / "favicons" / "nothing.test.missing"
    os.utime(marker, (clock.t, clock.t))
    clock.t += RETRY_MISSING_AFTER + 1
    cache.icon_for("https://nothing.test/")
    assert calls == ["nothing.test", "nothing.test"]


def test_network_error_backs_off_and_writes_nothing(tmp_path):
    clock, calls = Clock(), []

    def fetch(domain):
        calls.append(domain)
        raise FetchError("offline")

    cache = make_cache(tmp_path, fetch, clock)
    cache.icon_for("https://github.com/")
    cache.icon_for("https://github.com/")
    assert calls == ["github.com"]
    assert not (tmp_path / "favicons").exists()
    clock.t += RETRY_ERROR_AFTER + 1
    cache.icon_for("https://github.com/")
    assert len(calls) == 2


def test_stale_icon_is_still_served_while_refreshing(tmp_path):
    clock, calls = Clock(), []
    cache = make_cache(tmp_path, lambda d: calls.append(d) or PNG, clock)
    cache.icon_for("https://github.com/")
    path = tmp_path / "favicons" / "github.com.icon"
    os.utime(path, (clock.t, clock.t))
    clock.t += REFRESH_AFTER + 1
    assert cache.icon_for("https://github.com/") == str(path)
    assert calls == ["github.com", "github.com"]


@pytest.mark.parametrize("icon", ["", "my-icon"])
def test_quicklinks_use_website_icon_unless_configured(host, icon):
    provider = QuickLinksProvider(host, icons=lambda url: "/cache/github.com.icon")
    provider.configure(Config(quicklinks=(QuickLink("GitHub", "https://github.com", "gh", icon),)))
    [result] = provider.query("gh")
    assert result.icon == (icon or "/cache/github.com.icon")


def test_quicklinks_fall_back_to_themed_icon(host):
    provider = QuickLinksProvider(host, icons=lambda url: None)
    provider.configure(Config(quicklinks=(QuickLink("GitHub", "https://github.com", "gh"),)))
    assert provider.query("gh")[0].icon == LINK_ICON
