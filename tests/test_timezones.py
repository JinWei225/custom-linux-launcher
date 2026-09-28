from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from launcher import timezones as tz
from launcher.config import parse_texts
from launcher.engine import Engine
from launcher.providers.base import CONVERSION_SCORE, Result
from launcher.providers.timezones import PLACE_ONLY_SCORE, TimezonesProvider

KL = ZoneInfo("Asia/Kuala_Lumpur")
NOW = datetime(2026, 9, 28, 10, 30, tzinfo=KL)  # Monday morning; New York is still Sunday


def parse(text, now=NOW):
    return tz.parse(text, now)


def key(place):
    return getattr(place.tz, "key", None)


# --- places -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, zone",
    [
        ("tokyo", "Asia/Tokyo"),
        ("Tokyo", "Asia/Tokyo"),
        ("kuala lumpur", "Asia/Kuala_Lumpur"),
        ("new york", "America/New_York"),
        ("new_york", "America/New_York"),
        ("asia/tokyo", "Asia/Tokyo"),
        ("buenos aires", "America/Argentina/Buenos_Aires"),
        # cities the tz database doesn't name
        ("beijing", "Asia/Shanghai"),
        ("seoul", "Asia/Seoul"),
        ("busan", "Asia/Seoul"),
        ("san francisco", "America/Los_Angeles"),
        ("sf", "America/Los_Angeles"),
        ("nyc", "America/New_York"),
        ("kl", "Asia/Kuala_Lumpur"),
        ("mumbai", "Asia/Kolkata"),
        ("kiev", "Europe/Kyiv"),
        # countries
        ("japan", "Asia/Tokyo"),
        ("malaysia", "Asia/Kuala_Lumpur"),
        ("china", "Asia/Shanghai"),
        ("korea", "Asia/Seoul"),
        ("south korea", "Asia/Seoul"),
        ("north korea", "Asia/Pyongyang"),
        ("usa", "America/New_York"),
        ("united states", "America/New_York"),
        ("uk", "Europe/London"),
        ("england", "Europe/London"),
        ("australia", "Australia/Sydney"),
        ("canada", "America/Toronto"),
        ("russia", "Europe/Moscow"),
        ("brazil", "America/Sao_Paulo"),
        ("india", "Asia/Kolkata"),
        # abbreviations stand for a zone (with its daylight saving time)
        ("pst", "America/Los_Angeles"),
        ("PDT", "America/Los_Angeles"),
        ("est", "America/New_York"),
        ("cet", "Europe/Berlin"),
        ("jst", "Asia/Tokyo"),
        ("kst", "Asia/Seoul"),
        ("myt", "Asia/Kuala_Lumpur"),
        ("ist", "Asia/Kolkata"),
        ("aest", "Australia/Sydney"),
    ],
)
def test_find_places(name, zone):
    place = tz.places().find(name)
    assert place is not None and key(place) == zone


@pytest.mark.parametrize(
    "name, offset, label",
    [
        ("utc", timedelta(0), "UTC"),
        ("GMT", timedelta(0), "UTC"),
        ("utc+9", timedelta(hours=9), "UTC+9"),
        ("utc +9", timedelta(hours=9), "UTC+9"),
        ("gmt-5", timedelta(hours=-5), "UTC-5"),
        ("utc+5:30", timedelta(hours=5, minutes=30), "UTC+5:30"),
        ("utc+0530", timedelta(hours=5, minutes=30), "UTC+5:30"),
        ("utc+14", timedelta(hours=14), "UTC+14"),
    ],
)
def test_utc_offsets(name, offset, label):
    place = tz.places().find(name)
    assert place.tz.utcoffset(None) == offset
    assert place.label == label


@pytest.mark.parametrize("name", ["utc+15", "utc+5:75", "nowhere", "", "firefox"])
def test_unknown_places(name):
    assert tz.places().find(name) is None


def test_labels():
    find = tz.places().find
    assert find("kuala lumpur").label == "Kuala Lumpur"
    assert find("beijing").label == "Beijing"
    assert find("nyc").label == "NYC"
    assert find("japan").label == "Japan"
    assert find("usa").label == "United States (New York)"
    pst = find("pst")
    assert pst.name_at(datetime(2026, 1, 15, tzinfo=UTC)) == "Los Angeles (PST)"
    assert pst.name_at(datetime(2026, 7, 15, tzinfo=UTC)) == "Los Angeles (PDT)"


def test_places_work_without_the_tzdata_tables(tmp_path):
    lookup = tz.Places(tzdata=tmp_path)  # no zone.tab / iso3166.tab: no countries
    assert key(lookup.find("tokyo")) == "Asia/Tokyo"
    assert key(lookup.find("beijing")) == "Asia/Shanghai"
    assert lookup.find("japan") is None


def test_system_zone_prefers_tz_variable(monkeypatch):
    monkeypatch.setenv("TZ", ":Asia/Tokyo")
    assert tz.system_zone().key == "Asia/Tokyo"
    monkeypatch.setenv("TZ", "Not/AZone")
    assert tz.system_zone() is not None


# --- queries -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "time in tokyo",
        "Time in Tokyo",
        "what time is it in tokyo?",
        "current time in tokyo",
        "tokyo time",
        "now in tokyo",
        "tokyo now",
        "time in japan",
        "time in jst",
        "time in utc+9",
    ],
)
def test_current_time_explicit(text):
    answer = parse(text)
    assert isinstance(answer, tz.CurrentTime) and answer.explicit
    assert answer.place.tz.utcoffset(NOW.replace(tzinfo=None)) == timedelta(hours=9)


@pytest.mark.parametrize("text", ["tokyo", "london", "japan", "kuala lumpur", "beijing"])
def test_bare_place_names_are_weak(text):
    answer = parse(text)
    assert isinstance(answer, tz.CurrentTime) and not answer.explicit


@pytest.mark.parametrize(
    "text",
    [
        # too short or not a name: could be an app search
        "sf",
        "nyc",
        "kl",
        "uk",
        "pst",
        "utc",
        "asia/tokyo",
        # not about time at all
        "firefox",
        "code",
        "reading",
        "victoria",
        "3pm",  # a time without a place
        "15:00",
        "3",
        "tomorrow",
        "3pm nowhere",
        "3pm 4pm tokyo",
        "100 usd to myr",
        "",
    ],
)
def test_not_time_queries(text):
    assert parse(text) is None


@pytest.mark.parametrize(
    "text, hour, minute",
    [
        ("3pm tokyo", 15, 0),
        ("3 pm tokyo", 15, 0),
        ("3 p.m. tokyo", 15, 0),
        ("3PM Tokyo", 15, 0),
        ("3pm in tokyo", 15, 0),
        ("at 3pm in tokyo", 15, 0),
        ("tokyo 3pm", 15, 0),
        ("3pm tokyo time", 15, 0),
        ("15:00 tokyo", 15, 0),
        ("9:30am tokyo", 9, 30),
        ("12am tokyo", 0, 0),
        ("12pm tokyo", 12, 0),
        ("noon tokyo", 12, 0),
        ("midnight tokyo", 0, 0),
        ("0:05 tokyo", 0, 5),
    ],
)
def test_time_in_place_to_local(text, hour, minute):
    answer = parse(text)
    assert isinstance(answer, tz.Conversion)
    assert answer.target is None  # shown in local time
    assert key(answer.source) == "Asia/Tokyo"
    assert (answer.moment.hour, answer.moment.minute) == (hour, minute)
    assert answer.moment.date() == date(2026, 9, 28)


@pytest.mark.parametrize("text", ["13pm tokyo", "0am tokyo", "24:00 tokyo", "3:60pm tokyo"])
def test_invalid_clock_times(text):
    assert not isinstance(parse(text), tz.Conversion)


def test_day_is_the_places_own_today():
    # 10:30 Monday in KL is still Sunday in New York.
    answer = parse("3pm new york")
    assert answer.moment == datetime(2026, 9, 27, 15, 0, tzinfo=ZoneInfo("America/New_York"))
    answer = parse("tomorrow 9am new york")
    assert answer.moment == datetime(2026, 9, 28, 9, 0, tzinfo=ZoneInfo("America/New_York"))


@pytest.mark.parametrize(
    "text",
    [
        "tomorrow 9am new york",
        "9am tomorrow new york",
        "new york 9am tomorrow",
        "new york tomorrow at 9am",
        "9am on tomorrow in new york",
    ],
)
def test_day_phrase_in_any_order(text):
    answer = parse(text)
    assert isinstance(answer, tz.Conversion), text
    assert answer.moment == datetime(2026, 9, 28, 9, 0, tzinfo=ZoneInfo("America/New_York"))


def test_written_day_with_time():
    answer = parse("25 dec 8pm new york to tokyo")
    assert answer.moment == datetime(2026, 12, 25, 20, 0, tzinfo=ZoneInfo("America/New_York"))
    assert key(answer.target) == "Asia/Tokyo"
    answer = parse("next friday 3pm la")
    assert answer.moment.date() == date(2026, 10, 2)


@pytest.mark.parametrize(
    "text, source, target",
    [
        ("3pm pst to london", "America/Los_Angeles", "Europe/London"),
        ("3pm pst in london", "America/Los_Angeles", "Europe/London"),
        ("3pm pst into london", "America/Los_Angeles", "Europe/London"),
        ("15:00 seoul to new york", "Asia/Seoul", "America/New_York"),
        ("3pm to london", None, "Europe/London"),  # from local time
        ("now to london", None, "Europe/London"),
    ],
)
def test_between_two_places(text, source, target):
    answer = parse(text)
    assert isinstance(answer, tz.Conversion)
    assert (key(answer.source) if answer.source else None) == source
    assert key(answer.target) == target


def test_now_in_place_to_another():
    answer = parse("now tokyo to london")
    assert answer.moment == NOW
    assert key(answer.source) == "Asia/Tokyo"


def test_daylight_saving_time_is_followed():
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=KL)
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=KL)
    assert parse("3pm pst", winter).moment.utcoffset() == timedelta(hours=-8)
    assert parse("3pm pst", summer).moment.utcoffset() == timedelta(hours=-7)
    assert parse("3pm london", winter).moment.utcoffset() == timedelta(0)
    assert parse("3pm london", summer).moment.utcoffset() == timedelta(hours=1)


def test_time_skipped_by_dst_moves_on():
    # New York springs forward at 2:00 on 14 March 2027: 2:30 doesn't exist.
    answer = parse("2027-03-14 2:30am new york")
    assert (answer.moment.hour, answer.moment.minute) == (3, 30)
    assert answer.moment.utcoffset() == timedelta(hours=-4)


# --- formatting -------------------------------------------------------------------------


def test_format_time():
    t = datetime(2026, 9, 28, 15, 5)
    assert tz.format_time(t, clock_24h=True) == "15:05"
    assert tz.format_time(t, clock_24h=False) == "3:05 PM"
    assert tz.format_time(t.replace(hour=0), clock_24h=False) == "12:05 AM"
    assert tz.format_time(t.replace(hour=12), clock_24h=False) == "12:05 PM"
    assert tz.format_time(t.replace(hour=7), clock_24h=True) == "07:05"


def test_format_offset_and_difference():
    tokyo = NOW.astimezone(ZoneInfo("Asia/Tokyo"))
    delhi = NOW.astimezone(ZoneInfo("Asia/Kolkata"))
    kathmandu = NOW.astimezone(ZoneInfo("Asia/Kathmandu"))
    assert tz.format_offset(tokyo) == "UTC+9"
    assert tz.format_offset(delhi) == "UTC+5:30"
    assert tz.format_offset(NOW.astimezone(UTC)) == "UTC"
    assert tz.format_offset(NOW.astimezone(ZoneInfo("America/New_York"))) == "UTC-4"
    assert tz.difference(tokyo, NOW) == "1h ahead"
    assert tz.difference(delhi, NOW) == "2h 30m behind"
    assert tz.difference(kathmandu, delhi) == "15m ahead"
    assert tz.difference(NOW, NOW) == "same time"


def test_day_shift():
    d = date(2026, 9, 28)
    assert tz.day_shift(datetime(2026, 9, 28, 1), d) == ""
    assert tz.day_shift(datetime(2026, 9, 29, 1), d) == " (next day)"
    assert tz.day_shift(datetime(2026, 9, 27, 1), d) == " (previous day)"
    assert tz.day_shift(datetime(2026, 9, 30, 1), d) == " (+2 days)"


# --- provider ---------------------------------------------------------------------------


def provider(host, clock_24h=True, config=""):
    p = TimezonesProvider(
        host, clock_24h=lambda: clock_24h, now=lambda: NOW.astimezone(UTC),
        local_zone=lambda: KL,
    )  # fmt: skip
    p.configure(parse_texts(config, None)[0])
    return p


def test_provider_current_time(host):
    (result,) = provider(host).query("time in tokyo")
    assert result.title == "11:30"
    assert result.subtitle == "Tokyo · Mon 28 Sep · 1h ahead of you · UTC+9"
    assert result.score == CONVERSION_SCORE and not result.learn
    result.action()
    result.alt_action()
    assert host.calls == [("copy", "11:30"), ("paste-text", "11:30")]


def test_provider_current_time_behind_and_same(host):
    (ny,) = provider(host).query("time in new york")
    assert ny.subtitle == "New York · Sun 27 Sep · 12h behind you · UTC-4"
    (kl,) = provider(host).query("time in kl")
    assert kl.subtitle == "KL · Mon 28 Sep · same time as you · UTC+8"


def test_provider_offset_place_is_not_repeated(host):
    (result,) = provider(host).query("time in utc+9")
    assert result.subtitle == "UTC+9 · Mon 28 Sep · 1h ahead of you"


def test_provider_bare_place_ranks_low(host):
    (result,) = provider(host).query("tokyo")
    assert result.score == PLACE_ONLY_SCORE


def test_provider_to_local(host):
    (result,) = provider(host).query("3pm tokyo")
    assert result.title == "14:00"
    assert result.subtitle == "Kuala Lumpur, Mon 28 Sep · from 15:00 Tokyo, 1h ahead"


def test_provider_shows_day_change_and_copies_time_only(host):
    (result,) = provider(host).query("3pm new york")
    assert result.title == "03:00 (next day)"
    assert result.subtitle == "Kuala Lumpur, Mon 28 Sep · from 15:00 New York, 12h behind"
    result.action()
    assert host.calls == [("copy", "03:00")]


def test_provider_between_places(host):
    (result,) = provider(host).query("3pm pst to london")
    assert result.title == "23:00"
    assert result.subtitle == "London, Sun 27 Sep · from 15:00 Los Angeles (PDT), 8h behind"
    (result,) = provider(host).query("3pm to london")
    assert result.subtitle == "London, Mon 28 Sep · from 15:00 here, 7h ahead"


def test_provider_12_hour_clock(host):
    (result,) = provider(host, clock_24h=False).query("3pm new york")
    assert result.title == "3:00 AM (next day)"
    assert "from 3:00 PM New York" in result.subtitle


def test_provider_ignores_other_text_and_can_be_turned_off(host):
    assert provider(host).query("firefox") == []
    assert provider(host).query("  ") == []
    off = provider(host, config="[converters]\ntimezones = false")
    assert off.query("time in tokyo") == []


class _Apps:
    name = "apps"

    def query(self, text):
        if "tok" in text:
            return [Result(id="app:tokodon", title="Tokodon", score=0.35)]
        return []


def test_engine_puts_real_matches_above_a_bare_place(host):
    engine = Engine({"apps": _Apps(), "timezones": provider(host)})
    assert [r.id for r in engine.query("tokyo", "all", 8)] == ["app:tokodon", "timezone"]
    assert [r.id for r in engine.query("time in tokyo", "all", 8)] == [
        "timezone",
        "app:tokodon",
    ]
