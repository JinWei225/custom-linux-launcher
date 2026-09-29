import io
import json
import urllib.error

import pytest

from launcher import currency
from launcher.config import ConfigError, parse_texts
from launcher.currency import (
    RETRY_ERROR_AFTER,
    CurrencyQuery,
    FetchError,
    Rates,
    RatesCache,
    evaluate,
    parse,
)
from launcher.providers.base import CONVERSION_SCORE
from launcher.providers.currency import CurrencyProvider

DAY = 86400.0
T0 = 1_790_000_000.0
RATES = {"USD": 1.0, "MYR": 4.0, "JPY": 150.0, "EUR": 0.8, "KRW": 1400.0, "GBP": 0.75,
         "SGD": 1.3, "CNY": 7.0, "TOP": 2.4, "ALL": 90.0}  # fmt: skip
CODES = set(RATES)


class InlineExecutor:
    """Runs submitted work immediately so tests are deterministic."""

    def __init__(self):
        self.submitted = 0

    def submit(self, fn, *args):
        self.submitted += 1
        fn(*args)

    def shutdown(self, **_):
        pass


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Fetcher:
    def __init__(self, result=None):
        self.result = result or Rates(dict(RATES), T0 - 3600, T0)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_cache(tmp_path, fetch=None, clock=None, updates=None):
    return RatesCache(
        tmp_path / "rates.json",
        fetch=fetch or Fetcher(),
        on_update=(lambda: updates.append(1)) if updates is not None else None,
        executor=InlineExecutor(),
        now=clock or Clock(),
    )


# --- parsing ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("100 usd", CurrencyQuery(100, "USD", None)),
        ("100 USD", CurrencyQuery(100, "USD", None)),
        ("100usd", CurrencyQuery(100, "USD", None)),
        ("100 usd to myr", CurrencyQuery(100, "USD", "MYR")),
        ("100 usd in jpy", CurrencyQuery(100, "USD", "JPY")),
        ("100 usd into jpy", CurrencyQuery(100, "USD", "JPY")),
        ("100 usd as jpy", CurrencyQuery(100, "USD", "JPY")),
        ("100 usd -> jpy", CurrencyQuery(100, "USD", "JPY")),
        ("100 usd = jpy", CurrencyQuery(100, "USD", "JPY")),
        ("usd to myr", CurrencyQuery(1, "USD", "MYR")),  # amount defaults to 1
        ("12.5 eur", CurrencyQuery(12.5, "EUR", None)),
        ("1,234.50 usd", CurrencyQuery(1234.5, "USD", None)),
        ("1,234,567 jpy", CurrencyQuery(1234567, "JPY", None)),
        ("-5 usd", CurrencyQuery(-5, "USD", None)),
        # names
        ("100 dollars", CurrencyQuery(100, "USD", None)),
        ("1 dollar", CurrencyQuery(1, "USD", None)),
        ("50 ringgit", CurrencyQuery(50, "MYR", None)),
        ("1000 won", CurrencyQuery(1000, "KRW", None)),
        ("200 yuan", CurrencyQuery(200, "CNY", None)),
        ("100 yen", CurrencyQuery(100, "JPY", None)),
        ("20 euros", CurrencyQuery(20, "EUR", None)),
        ("5 pounds", CurrencyQuery(5, "GBP", None)),
        ("10 singapore dollars", CurrencyQuery(10, "SGD", None)),
        ("10 us dollars to singapore dollars", CurrencyQuery(10, "USD", "SGD")),
        ("100 yen to ringgit", CurrencyQuery(100, "JPY", "MYR")),
        # shorthand and arithmetic
        ("1.5k usd", CurrencyQuery(1500, "USD", None, "1.5k")),
        ("2m jpy", CurrencyQuery(2_000_000, "JPY", None, "2m")),
        ("3 bn krw", CurrencyQuery(3e9, "KRW", None, "3 bn")),
        ("(12+30)*3 usd", CurrencyQuery(126, "USD", None, "(12+30)*3")),
        ("2 x 19.99 euros to myr", CurrencyQuery(39.98, "EUR", "MYR", "2 x 19.99")),
        ("100/4 usd", CurrencyQuery(25, "USD", None, "100/4")),
    ],
)
def test_parse(text, expected):
    got = parse(text, CODES)
    assert got is not None, text
    assert (got.source, got.target, got.expression) == (
        expected.source,
        expected.target,
        expected.expression,
    )
    assert got.amount == pytest.approx(expected.amount)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "100",
        "usd",  # no amount and no target
        "firefox",
        "3pm tokyo",
        "tomorrow",
        "100 xyz",
        "100 btc",  # not in the rates
        "100 usd to xyz",
        "1/0 usd",
        "(1 usd",
        "1e999 usd",
        "10 top",  # "top" is a word; lower case needs a target
        "100 all",
        "import os usd",
        "__import__('os') usd",
        "100 peso",  # ambiguous: needs the country
    ],
)
def test_not_currency(text):
    assert parse(text, CODES) is None


def test_word_codes_count_in_upper_case_or_with_a_target():
    assert parse("10 TOP", CODES) == CurrencyQuery(10, "TOP", None)
    assert parse("10 top to usd", CODES) == CurrencyQuery(10, "TOP", "USD")


@pytest.mark.parametrize(
    "text, value",
    [
        ("42", 42),
        ("1.5k", 1500),
        ("1.5 k", 1500),
        ("2 million", 2e6),
        ("(12+30)*3", 126),
        ("2 x 3", 6),
        ("2*3+4", 10),
        ("2+3*4", 14),
        ("10-2-3", 5),
        ("2^10", 1024),
        ("-(3+4)", -7),
        (".5", 0.5),
        ("7/2", 3.5),
    ],
)
def test_evaluate(text, value):
    assert evaluate(text) == pytest.approx(value)


@pytest.mark.parametrize("text", ["", "1/0", "2^99", "(1", "1)", "1 +", "abc", "1e5", "9" * 20])
def test_evaluate_rejects(text):
    assert evaluate(text) is None


# --- formatting --------------------------------------------------------------------------


def test_format_amount():
    assert currency.format_amount(4723.5, "MYR") == "4,723.50"
    assert currency.format_amount(4723.5, "MYR", grouping=False) == "4723.50"
    assert currency.format_amount(15749.3, "JPY") == "15,749"
    assert currency.format_amount(0.635, "USD") == "0.635"
    assert currency.format_amount(0.003005, "MYR") == "0.00300"
    assert currency.format_amount(0.5, "KRW") == "0.500"
    assert currency.format_amount(0, "USD") == "0.00"
    assert currency.format_amount(-20.37, "MYR") == "-20.37"


def test_format_number_and_rate():
    assert currency.format_number(126.0) == "126"
    assert currency.format_number(1500) == "1,500"
    assert currency.format_number(1234.5) == "1,234.5"
    assert currency.format_rate(4.07444) == "4.0744"
    assert currency.format_rate(157.48941) == "157.4894"
    assert currency.format_rate(0.0063502) == "0.006350"


def test_age():
    assert currency.age(T0, T0 + 3600) == "today"
    assert currency.age(T0, T0 + DAY + 1) == "yesterday"
    assert currency.age(T0, T0 + 3 * DAY + 1) == "3 days ago"


def test_home_currency(tmp_path):
    (tmp_path / "zone.tab").write_text(
        "# comment\nMY\t+0310+10142\tAsia/Kuala_Lumpur\nJP\t+353916+1394441\tAsia/Tokyo\n"
        "AQ\t-7750+16636\tAntarctica/McMurdo\n"
    )
    assert currency.home_currency("", "Asia/Kuala_Lumpur", tmp_path) == "MYR"
    assert currency.home_currency("", "Asia/Tokyo", tmp_path) == "JPY"
    assert currency.home_currency("sgd", "Asia/Tokyo", tmp_path) == "SGD"
    assert currency.home_currency("", "Antarctica/McMurdo", tmp_path) == "USD"
    assert currency.home_currency("", None, tmp_path) == "USD"
    assert currency.home_currency("", "Asia/Tokyo", tmp_path / "missing") == "USD"


# --- rates cache -------------------------------------------------------------------------


def test_first_query_downloads_and_stores(tmp_path):
    updates = []
    fetch = Fetcher()
    cache = make_cache(tmp_path, fetch, updates=updates)
    rates = cache.rates()
    assert fetch.calls == 1 and updates == [1]
    assert rates.convert(100, "USD", "MYR") == pytest.approx(400)
    assert rates.convert(150, "JPY", "EUR") == pytest.approx(0.8)
    stored = json.loads((tmp_path / "rates.json").read_text())
    assert stored["rates"]["MYR"] == 4.0 and stored["fetched"] == T0


def test_rates_are_loaded_from_disk_and_not_refetched_while_fresh(tmp_path):
    make_cache(tmp_path).rates()
    fetch = Fetcher()
    clock = Clock(T0 + 5 * 3600)
    cache = make_cache(tmp_path, fetch, clock)
    assert cache.rates().rates["JPY"] == 150.0
    assert fetch.calls == 0
    clock.t = T0 + 6 * 3600  # refresh_after (6 h) has passed
    cache.rates()
    assert fetch.calls == 1


def test_refresh_after_follows_the_setting(tmp_path):
    make_cache(tmp_path).rates()
    fetch = Fetcher()
    cache = make_cache(tmp_path, fetch, Clock(T0 + 2 * 3600))
    cache.refresh_after = 1 * 3600
    cache.rates()
    assert fetch.calls == 1


def test_failed_download_keeps_old_rates_and_backs_off(tmp_path):
    make_cache(tmp_path).rates()
    fetch = Fetcher(FetchError("offline"))
    clock = Clock(T0 + DAY)
    cache = make_cache(tmp_path, fetch, clock)
    assert cache.rates().rates["MYR"] == 4.0  # the old ones
    assert fetch.calls == 1 and cache.last_error == (T0 + DAY, "offline")
    clock.t += RETRY_ERROR_AFTER - 1
    cache.rates()
    assert fetch.calls == 1  # not hammering the network
    clock.t += 2
    fetch.result = Rates({"USD": 1.0, "MYR": 4.5}, clock.t, clock.t)
    assert cache.rates().rates["MYR"] == 4.5
    assert cache.last_error is None


def test_unexpected_errors_are_contained(tmp_path):
    cache = make_cache(tmp_path, Fetcher(RuntimeError("bug")))
    assert cache.rates() is None
    assert cache.last_error[1] == "bug"


def test_explicit_refresh_ignores_age(tmp_path):
    fetch = Fetcher()
    cache = make_cache(tmp_path, fetch)
    cache.rates()
    cache.refresh()
    assert fetch.calls == 2


def test_no_second_download_while_one_runs(tmp_path):
    class Deferred:
        def __init__(self):
            self.jobs = []

        def submit(self, fn, *args):
            self.jobs.append(fn)

    executor = Deferred()
    cache = RatesCache(tmp_path / "rates.json", fetch=Fetcher(), executor=executor)
    cache.rates()
    cache.rates()
    cache.refresh()
    assert len(executor.jobs) == 1
    executor.jobs[0]()
    cache.refresh()
    assert len(executor.jobs) == 2


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "{}",
        '{"updated": 1, "fetched": 1, "rates": {"MYR": 4}}',  # no USD
        '{"updated": 1, "fetched": 1, "rates": []}',
    ],
)
def test_unreadable_cache_file_is_ignored(tmp_path, content):
    (tmp_path / "rates.json").write_text(content)
    fetch = Fetcher()
    cache = make_cache(tmp_path, fetch)
    assert cache.rates() is not None
    assert fetch.calls == 1


def test_bad_rate_values_are_dropped():
    rates = currency._valid_rates(
        {"USD": 1, "MYR": 4.1, "BAD": -1, "NAN": float("nan"), "X": 2, "TRU": True, "ZER": 0}
    )
    assert rates == {"USD": 1.0, "MYR": 4.1}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def test_fetch_rates_parses_the_api_answer(monkeypatch):
    body = {
        "result": "success",
        "time_last_update_unix": 1790553751,
        "base_code": "USD",
        "rates": {"USD": 1, "MYR": 4.0744, "JPY": 157.49},
    }
    seen = []

    def urlopen(request, timeout):
        seen.append((request.full_url, timeout))
        return _Response(json.dumps(body).encode())

    monkeypatch.setattr(currency.urllib.request, "urlopen", urlopen)
    rates = currency.fetch_rates(now=T0)
    assert seen == [(currency.RATES_URL, currency.TIMEOUT_SECONDS)]
    assert rates == Rates({"USD": 1.0, "MYR": 4.0744, "JPY": 157.49}, 1790553751.0, T0)


@pytest.mark.parametrize(
    "outcome",
    [
        urllib.error.URLError("no network"),
        urllib.error.HTTPError(currency.RATES_URL, 429, "Too Many Requests", {}, None),
        TimeoutError(),
        b"<html>not json</html>",
        json.dumps({"result": "error", "error-type": "unsupported-code"}).encode(),
        json.dumps({"result": "success", "rates": {"MYR": 4}}).encode(),
    ],
)
def test_fetch_rates_failures_are_fetch_errors(monkeypatch, outcome):
    def urlopen(request, timeout):
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)

    monkeypatch.setattr(currency.urllib.request, "urlopen", urlopen)
    with pytest.raises(FetchError):
        currency.fetch_rates()


# --- provider --------------------------------------------------------------------------


def provider(host, tmp_path, config="", fetch=None, clock=None):
    cache = make_cache(tmp_path, fetch, clock)
    p = CurrencyProvider(host, cache, zone_key=lambda: "Asia/Kuala_Lumpur")
    p.configure(parse_texts(config, None)[0])
    return p


def test_provider_converts_to_home_currency(host, tmp_path):
    (result,) = provider(host, tmp_path).query("100 usd")
    assert result.title == "400.00 MYR"
    assert result.subtitle == "100 USD → MYR · 1 USD = 4.0000 MYR · rates from today"
    assert result.score == CONVERSION_SCORE and not result.learn
    result.action()
    result.alt_action()
    assert host.calls == [("copy", "400.00"), ("paste-text", "400.00")]


def test_provider_copies_without_thousands_separators(host, tmp_path):
    (result,) = provider(host, tmp_path).query("1.5k usd to jpy")
    assert result.title == "225,000 JPY"
    assert result.subtitle.startswith("1.5k = 1,500 USD → JPY")
    result.action()
    assert host.calls == [("copy", "225000")]


def test_provider_home_currency_setting(host, tmp_path):
    p = provider(host, tmp_path, config='[converters]\nhome_currency = "SGD"')
    assert p.query("100 usd")[0].title == "130.00 SGD"


def test_provider_home_currency_amount_shows_dollars(host, tmp_path):
    (result,) = provider(host, tmp_path).query("100 myr")
    assert result.title == "25.00 USD"


def test_provider_explicit_target(host, tmp_path):
    (result,) = provider(host, tmp_path).query("7 yuan to euros")
    assert result.title == "0.800 EUR"


def test_provider_before_first_download(host, tmp_path):
    p = provider(host, tmp_path, fetch=Fetcher(FetchError("offline")))
    (result,) = p.query("100 usd")
    assert result.title == "USD → MYR: no exchange rates yet"
    assert "Download failed (offline)" in result.subtitle
    assert result.action is None  # Enter does nothing (and keeps the window open)


def test_provider_says_when_rates_are_old_and_cannot_update(host, tmp_path):
    make_cache(tmp_path).rates()
    clock = Clock(T0 + 3 * DAY)
    p = provider(host, tmp_path, fetch=Fetcher(FetchError("offline")), clock=clock)
    (result,) = p.query("100 usd")
    assert result.title == "400.00 MYR"
    assert result.subtitle.endswith("rates from 3 days ago (can't update: offline?)")


def test_provider_ignores_other_text_and_can_be_turned_off(host, tmp_path):
    p = provider(host, tmp_path)
    assert p.query("firefox") == [] and p.query("100") == [] and p.query(" ") == []
    off = provider(host, tmp_path, config="[converters]\ncurrency = false")
    assert off.query("100 usd") == []


def test_provider_does_not_download_for_unrelated_typing(host, tmp_path):
    fetch = Fetcher(FetchError("offline"))
    cache = RatesCache(tmp_path / "rates.json", fetch=fetch, executor=InlineExecutor())
    p = CurrencyProvider(host, cache)
    p.query("firefox")
    assert fetch.calls == 0


def test_configure_starts_the_first_download(host, tmp_path):
    fetch = Fetcher()
    provider(host, tmp_path, fetch=fetch)
    assert fetch.calls == 1


def test_config_validation():
    config, _ = parse_texts("", None)
    assert (config.converters.currency, config.converters.home_currency) == (True, "")
    assert config.converters.refresh_hours == 6
    config, _ = parse_texts('[converters]\nhome_currency = "myr"\nrefresh_hours = 24', None)
    assert (config.converters.home_currency, config.converters.refresh_hours) == ("myr", 24)
    for bad in ('home_currency = "ringgit"', 'home_currency = "M1R"', "refresh_hours = 0"):
        with pytest.raises(ConfigError):
            parse_texts(f"[converters]\n{bad}", None)


@pytest.mark.parametrize("text", ["(-8)^0.5", "(-2)^(1/3)", "(" * 5000 + "1" + ")" * 5000])
def test_evaluate_rejects_what_has_no_real_answer(text):
    from launcher.currency import evaluate

    assert evaluate(text) is None


def test_evaluate_negative_base_whole_exponent():
    from launcher.currency import evaluate

    assert evaluate("(-2)^3") == -8
    assert evaluate("(-2)^2") == 4


def test_provider_gives_no_answer_for_a_complex_amount(host, tmp_path):
    assert provider(host, tmp_path).query("(-8)^0.5 usd") == []
