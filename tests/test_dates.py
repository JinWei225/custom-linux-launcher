from datetime import date

import pytest

from launcher import dates
from launcher.config import ConfigError, parse_texts
from launcher.dates import DateAnswer, DaysAnswer, Duration, add, parse
from launcher.engine import Engine
from launcher.providers.base import ALIAS_SCORE, CONVERSION_SCORE
from launcher.providers.dates import DatesProvider

TODAY = date(2026, 9, 28)  # a Monday


def d(text, today=TODAY):
    answer = parse(text, today)
    assert isinstance(answer, DateAnswer), f"{text!r} -> {answer}"
    return answer.date


def days(text, today=TODAY):
    answer = parse(text, today)
    assert isinstance(answer, DaysAnswer), f"{text!r} -> {answer}"
    return answer.days


# --- single days ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("today", date(2026, 9, 28)),
        ("now", date(2026, 9, 28)),
        ("tomorrow", date(2026, 9, 29)),
        ("tmr", date(2026, 9, 29)),
        ("yesterday", date(2026, 9, 27)),
        ("day after tomorrow", date(2026, 9, 30)),
        ("the day after tomorrow", date(2026, 9, 30)),
        ("day-after-tomorrow", date(2026, 9, 30)),
        ("day before yesterday", date(2026, 9, 26)),
        ("Tomorrow", date(2026, 9, 29)),
        ("  tomorrow  ", date(2026, 9, 29)),
    ],
)
def test_named_days(text, expected):
    assert d(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("in 3 days", date(2026, 10, 1)),
        ("in a week", date(2026, 10, 5)),
        ("in two weeks", date(2026, 10, 12)),
        ("in 1 fortnight", date(2026, 10, 12)),
        ("two months after today", date(2026, 11, 28)),
        ("2 months after today", date(2026, 11, 28)),
        ("2 months from now", date(2026, 11, 28)),
        ("3 days ago", date(2026, 9, 25)),
        ("a year ago", date(2025, 9, 28)),
        ("5 days later", date(2026, 10, 3)),
        ("twenty-one days from now", date(2026, 10, 19)),
        ("twenty one days from now", date(2026, 10, 19)),
        ("a couple of weeks ago", date(2026, 9, 14)),
        ("2 weeks and 3 days from now", date(2026, 10, 15)),
        ("1 year 2 months from today", date(2027, 11, 28)),
        ("a week from tomorrow", date(2026, 10, 6)),
        ("3 weeks before 2026-12-25", date(2026, 12, 4)),
        ("2 weeks after christmas", date(2027, 1, 8)),
        ("christmas - 3 days", date(2026, 12, 22)),
        ("christmas -3d", date(2026, 12, 22)),
        ("today + 10d", date(2026, 10, 8)),
        ("today plus 1 month minus 1 day", date(2026, 10, 27)),
        ("2 weeks after 3 days before christmas", date(2027, 1, 5)),
        # an offset from any written-out date
        ("one week after 26 October 2026", date(2026, 11, 2)),
        ("One week after 26th October 2026", date(2026, 11, 2)),
        ("1 week after october 26, 2026", date(2026, 11, 2)),
        ("3 days before 26/10/2026", date(2026, 10, 23)),
        ("2 months after 31 Jan 2027", date(2027, 3, 31)),
        ("a month after 31 January 2027", date(2027, 2, 28)),
        ("ten days from 2026-10-26", date(2026, 11, 5)),
        ("26 october 2026 + 1 week", date(2026, 11, 2)),
    ],
)
def test_offsets(text, expected):
    assert d(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        # "friday" / "next friday": the first one after today
        ("friday", date(2026, 10, 2)),
        ("next friday", date(2026, 10, 2)),
        ("coming friday", date(2026, 10, 2)),
        ("next fri", date(2026, 10, 2)),
        # today is Monday: "monday" is next week's, "this monday" is today
        ("monday", date(2026, 10, 5)),
        ("this monday", date(2026, 9, 28)),
        ("this sunday", date(2026, 10, 4)),
        ("last friday", date(2026, 9, 25)),
        ("last monday", date(2026, 9, 21)),
        ("next week", date(2026, 10, 5)),
        ("next month", date(2026, 10, 28)),
        ("next year", date(2027, 9, 28)),
        ("last month", date(2026, 8, 28)),
    ],
)
def test_relative_weekdays_and_periods(text, expected):
    assert d(text) == expected


@pytest.mark.parametrize("text", ["fri", "sun", "wed", "sat", "mon"])
def test_bare_weekday_abbreviations_are_not_dates(text):
    # So typing "sun" or "wed" still searches apps.
    assert parse(text, TODAY) is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("last monday of october", date(2026, 10, 26)),
        ("first monday of october", date(2026, 10, 5)),
        ("2nd tue in march 2027", date(2027, 3, 9)),
        ("first monday of june", date(2027, 6, 7)),  # June has passed: next year's
        ("last friday of next month", date(2026, 10, 30)),
        ("third thursday of this month", date(2026, 9, 17)),
        ("end of month", date(2026, 9, 30)),
        ("end of the month", date(2026, 9, 30)),
        ("start of next month", date(2026, 10, 1)),
        ("beginning of next year", date(2027, 1, 1)),
        ("end of year", date(2026, 12, 31)),
        ("end of next week", date(2026, 10, 11)),
        ("start of this week", date(2026, 9, 28)),
        ("end of february", date(2027, 2, 28)),
        ("end of february 2028", date(2028, 2, 29)),
    ],
)
def test_month_positions(text, expected):
    assert d(text) == expected


def test_missing_fifth_weekday_is_no_answer():
    assert parse("fifth friday of february", TODAY) is None  # Feb 2027 has four


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-12-25", date(2026, 12, 25)),
        ("2027-1-5", date(2027, 1, 5)),
        ("25/12", date(2026, 12, 25)),  # day first
        ("1/2/2027", date(2027, 2, 1)),
        ("1/2/27", date(2027, 2, 1)),
        ("dec 25", date(2026, 12, 25)),
        ("December 25, 2027", date(2027, 12, 25)),
        ("25 december 2027", date(2027, 12, 25)),
        ("25th of dec", date(2026, 12, 25)),
        ("the 3rd of sept", date(2027, 9, 3)),  # already passed this year
        ("may 5", date(2027, 5, 5)),
        ("sep 28", date(2026, 9, 28)),  # today counts
        ("feb 29", date(2028, 2, 29)),  # next leap year
        ("what date is 2 weeks from now", date(2026, 10, 12)),
        ("when is christmas?", date(2026, 12, 25)),
    ],
)
def test_calendar_dates(text, expected):
    assert d(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("christmas", date(2026, 12, 25)),
        ("xmas", date(2026, 12, 25)),
        ("christmas day", date(2026, 12, 25)),
        ("christmas eve", date(2026, 12, 24)),
        ("boxing day", date(2026, 12, 26)),
        ("new year", date(2027, 1, 1)),
        ("new year's day", date(2027, 1, 1)),
        ("new year’s eve", date(2026, 12, 31)),
        ("valentine's day", date(2027, 2, 14)),
        ("april fools", date(2027, 4, 1)),
        ("halloween", date(2026, 10, 31)),
        ("halloween 2030", date(2030, 10, 31)),
        ("new year 2027", date(2027, 1, 1)),
    ],
)
def test_fixed_holidays(text, expected):
    assert d(text) == expected


def test_holiday_on_the_day_is_today():
    assert d("christmas", today=date(2026, 12, 25)) == date(2026, 12, 25)
    assert d("christmas", today=date(2026, 12, 26)) == date(2027, 12, 25)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "firefox",
        "code",
        "may",  # a month alone is too vague (and a common word)
        "2026",
        "12",
        "3pm tokyo",
        "100 usd",
        "100 usd to myr",
        "31/2",
        "nov 31",
        "2026-02-30",
        "99999 years from now",
        "tomorrow tomorrow",
        "in",
        "3 days",  # a duration needs "in", "ago", "from ..."
        "25.12.2026",
        "#tomorrow",
    ],
)
def test_not_dates(text):
    assert parse(text, TODAY) is None


# --- day counts ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("days until christmas", 88),
        ("how many days until christmas", 88),
        ("days till xmas", 88),
        ("until friday", 4),
        ("days to 2026-12-25", 88),
        ("weeks until christmas", 88),
        ("2026-10-01 to 2026-12-25", 85),
        ("days between 2026-01-01 and 2026-12-31", 364),
        ("between 2026-01-01 and 2027-01-01", 365),
        ("days from 1/1/2026 to 1/3/2026", 59),
        ("days since 2026-01-01", 270),
        # "since" looks back: the friday / christmas / feb 29 before today
        ("days since friday", 3),
        ("days since christmas", 277),
        ("days since feb 29", 942),
        ("days until yesterday", -1),
    ],
)
def test_day_counts(text, expected):
    assert days(text) == expected


def test_day_count_endpoints():
    answer = parse("days until christmas", TODAY)
    assert answer == DaysAnswer(TODAY, date(2026, 12, 25))


# --- arithmetic and formatting ---------------------------------------------------------


@pytest.mark.parametrize(
    "start, duration, sign, expected",
    [
        (date(2026, 1, 31), Duration(months=1), 1, date(2026, 2, 28)),
        (date(2028, 1, 31), Duration(months=1), 1, date(2028, 2, 29)),
        (date(2026, 3, 31), Duration(months=1), -1, date(2026, 2, 28)),
        (date(2028, 2, 29), Duration(years=1), 1, date(2029, 2, 28)),
        (date(2026, 12, 15), Duration(months=1), 1, date(2027, 1, 15)),
        (date(2026, 1, 15), Duration(months=13), -1, date(2024, 12, 15)),
        (date(2026, 1, 31), Duration(months=1, days=1), 1, date(2026, 3, 1)),
    ],
)
def test_add_clamps_to_month_end(start, duration, sign, expected):
    assert add(start, duration, sign) == expected


def test_formatting():
    assert dates.long_form(date(2026, 11, 28)) == "Saturday, 28 November 2026"
    assert dates.short_form(date(2026, 11, 28)) == "Sat 28 Nov 2026"
    assert dates.relative(TODAY, TODAY) == "today"
    assert dates.relative(date(2026, 9, 29), TODAY) == "tomorrow"
    assert dates.relative(date(2026, 9, 27), TODAY) == "yesterday"
    assert dates.relative(date(2026, 11, 28), TODAY) == "in 61 days"
    assert dates.relative(date(2026, 9, 25), TODAY) == "3 days ago"
    assert dates.weeks_and_days(88) == "12 weeks 4 days"
    assert dates.weeks_and_days(14) == "2 weeks"
    assert dates.weeks_and_days(8) == "1 week 1 day"
    assert dates.weeks_and_days(3) == "3 days"


# --- provider --------------------------------------------------------------------------


def provider(host, **converters):
    p = DatesProvider(host, today=lambda: TODAY)
    config, _ = parse_texts(_converters_toml(**converters), None)
    p.configure(config)
    return p


def _converters_toml(**values):
    lines = ["[converters]"] + [f"{k} = {str(v).lower()}" for k, v in values.items()]
    return "\n".join(lines)


def test_provider_date_rows(host):
    results = provider(host).query("two months after today")
    assert [r.title for r in results] == ["2026-11-28", "Saturday, 28 November 2026"]
    assert results[0].subtitle == "Saturday, 28 November 2026 · in 61 days"
    assert results[0].score > results[1].score
    assert all(r.score >= CONVERSION_SCORE - 0.1 and not r.learn for r in results)


def test_enter_copies_and_alt_enter_pastes(host):
    top, long = provider(host).query("tomorrow")
    top.action()
    top.alt_action()
    long.action()
    assert host.calls == [
        ("copy", "2026-09-29"),
        ("paste-text", "2026-09-29"),
        ("copy", "Tuesday, 29 September 2026"),
    ]


def test_provider_day_count(host):
    (result,) = provider(host).query("days until christmas")
    assert result.title == "88 days"
    assert result.subtitle == "12 weeks 4 days · Mon 28 Sep 2026 → Fri 25 Dec 2026"
    result.action()
    assert host.calls == [("copy", "88")]


def test_provider_short_and_negative_day_counts(host):
    p = provider(host)
    (short,) = p.query("until friday")
    assert (short.title, short.subtitle) == ("4 days", "Mon 28 Sep 2026 → Fri 2 Oct 2026")
    (one,) = p.query("until tomorrow")
    assert one.title == "1 day"
    (past,) = p.query("days until yesterday")
    assert past.title == "1 day" and past.subtitle.endswith("already past")
    (future,) = p.query("days since next friday")
    assert future.subtitle.endswith("still to come")
    (backwards,) = p.query("2026-12-25 to 2026-12-01")
    assert backwards.subtitle.endswith("the second date is earlier")


def test_provider_ignores_other_text(host):
    p = provider(host)
    assert p.query("") == []
    assert p.query("firefox") == []


def test_provider_can_be_turned_off(host):
    assert provider(host, dates=False).query("tomorrow") == []


def test_config_defaults_and_validation():
    config, warnings = parse_texts("", None)
    assert config.converters.dates is True
    config, warnings = parse_texts("[converters]\ndates = false\nfoo = 1", None)
    assert config.converters.dates is False
    assert warnings == ["unknown setting 'converters.foo' ignored"]
    with pytest.raises(ConfigError):
        parse_texts('[converters]\ndates = "yes"', None)


class _Named:
    """A provider that matches its own name exactly (like an app called "Today")."""

    def __init__(self, name):
        self.name = name

    def query(self, text):
        from launcher.providers.base import Result

        if text.strip().casefold() == self.name:
            return [Result(id=f"app:{self.name}", title=self.name, score=1.0)]
        if text.strip().casefold() == "cal":
            return [Result(id="app:cal", title="cal", score=ALIAS_SCORE)]
        return []


def test_engine_ranks_date_first_but_below_exact_alias(host):
    engine = Engine({"apps": _Named("tomorrow"), "dates": provider(host)})
    top = engine.query("tomorrow", "all", 8)
    assert [r.id for r in top] == ["date:iso", "date:long", "app:tomorrow"]
    engine = Engine({"apps": _Named("x"), "dates": provider(host)})
    assert engine.query("cal", "all", 8)[0].id == "app:cal"
