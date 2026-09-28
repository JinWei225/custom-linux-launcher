"""Times across timezones: "time in tokyo", "3pm tokyo", "3pm pst to london".

Pure Python (zoneinfo). Places are looked up in, in this order:
  - UTC offsets: utc, gmt, utc+9, gmt-5:30
  - common abbreviations (pst, jst, cet...). Each one maps to a zone, not a fixed offset,
    so "3pm pst" in July means Pacific *daylight* time, as people mean it.
  - cities the tz database doesn't name (beijing, mumbai, san francisco, nyc...)
  - every tz database city (tokyo, kuala lumpur, new york) and zone id (asia/tokyo)
  - countries from the system's tzdata tables (japan, united states, uk)
"""

from __future__ import annotations

import functools
import os
import re
import zoneinfo
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from pathlib import Path

from . import dates

TZDATA = Path("/usr/share/zoneinfo")


@dataclass(frozen=True)
class Place:
    label: str  # "Tokyo", "Japan", "Los Angeles" (abbreviations add the zone's own name)
    tz: tzinfo
    kind: str  # "city" | "country" | "abbreviation" | "offset"

    def name_at(self, moment: datetime) -> str:
        """The label, with the abbreviation in force then: "Los Angeles (PDT)"."""
        if self.kind == "abbreviation":
            return f"{self.label} ({moment.astimezone(self.tz).tzname()})"
        return self.label


@dataclass(frozen=True)
class CurrentTime:
    """ "time in tokyo" (explicit) or just "tokyo" (not explicit: rank low)."""

    place: Place
    explicit: bool


@dataclass(frozen=True)
class Conversion:
    """A moment given in one place, shown in another (None = the local zone)."""

    moment: datetime  # aware, in the source place's zone
    source: Place | None
    target: Place | None


TimeAnswer = CurrentTime | Conversion


# --- places ---------------------------------------------------------------------------

# Abbreviation -> zone. Ambiguous ones get their most common meaning (CST = US Central,
# IST = India).
ABBREVIATIONS = {
    **dict.fromkeys(("pt", "pst", "pdt"), "America/Los_Angeles"),
    **dict.fromkeys(("mt", "mst", "mdt"), "America/Denver"),
    **dict.fromkeys(("ct", "cst", "cdt"), "America/Chicago"),
    **dict.fromkeys(("et", "est", "edt"), "America/New_York"),
    **dict.fromkeys(("akst", "akdt"), "America/Anchorage"),
    "hst": "Pacific/Honolulu",
    **dict.fromkeys(("bst",), "Europe/London"),
    **dict.fromkeys(("cet", "cest"), "Europe/Berlin"),
    **dict.fromkeys(("eet", "eest"), "Europe/Athens"),
    **dict.fromkeys(("wet", "west"), "Europe/Lisbon"),
    "msk": "Europe/Moscow",
    "gst": "Asia/Dubai",
    "pkt": "Asia/Karachi",
    "ist": "Asia/Kolkata",
    "ict": "Asia/Bangkok",
    "wib": "Asia/Jakarta",
    **dict.fromkeys(("sgt",), "Asia/Singapore"),
    **dict.fromkeys(("myt",), "Asia/Kuala_Lumpur"),
    **dict.fromkeys(("pht", "phst"), "Asia/Manila"),
    "hkt": "Asia/Hong_Kong",
    **dict.fromkeys(("jst",), "Asia/Tokyo"),
    **dict.fromkeys(("kst",), "Asia/Seoul"),
    "awst": "Australia/Perth",
    **dict.fromkeys(("acst", "acdt"), "Australia/Adelaide"),
    **dict.fromkeys(("aest", "aedt"), "Australia/Sydney"),
    **dict.fromkeys(("nzst", "nzdt"), "Pacific/Auckland"),
    "sast": "Africa/Johannesburg",
    "wat": "Africa/Lagos",
    "eat": "Africa/Nairobi",
    "brt": "America/Sao_Paulo",
    "art": "America/Argentina/Buenos_Aires",
}

# Cities (and regions) the tz database has no zone named after: zone -> "name|name".
_CITY_ALIAS_TABLE = {
    "Asia/Shanghai": "beijing|shenzhen|guangzhou|hangzhou|chengdu|wuhan|xian|nanjing",
    "Asia/Seoul": "busan|incheon",
    "Asia/Tokyo": "osaka|kyoto|nagoya|sapporo|fukuoka",
    "Asia/Kuala_Lumpur": "kl|penang|george town|johor bahru|ipoh",
    "Asia/Kuching": "kota kinabalu|sabah|sarawak",
    "Asia/Ho_Chi_Minh": "hanoi|saigon|da nang",
    "Asia/Makassar": "bali|denpasar",
    "Asia/Taipei": "kaohsiung|taichung",
    "Asia/Kolkata": "mumbai|bombay|delhi|new delhi|bangalore|bengaluru|chennai|hyderabad|pune"
    "|calcutta",
    "Asia/Dubai": "abu dhabi",
    "Asia/Jerusalem": "tel aviv",
    "Europe/Moscow": "st petersburg|saint petersburg",
    "Europe/London": "manchester|edinburgh|glasgow|birmingham",
    "Europe/Berlin": "munich|frankfurt|hamburg|cologne",
    "Europe/Madrid": "barcelona|valencia|seville",
    "Europe/Rome": "milan|venice|florence|naples",
    "Europe/Zurich": "geneva|basel",
    "Europe/Paris": "lyon|marseille",
    "Europe/Amsterdam": "rotterdam|the hague",
    "Europe/Kyiv": "kiev",
    "America/Los_Angeles": "san francisco|sf|la|seattle|san diego|san jose|las vegas|portland"
    "|silicon valley|california",
    "America/New_York": "nyc|new york city|boston|washington|washington dc|dc|miami|atlanta"
    "|philadelphia|pittsburgh",
    "America/Chicago": "houston|dallas|austin|san antonio|texas",
    "America/Denver": "salt lake city|colorado",
    "Pacific/Honolulu": "hawaii",
    "America/Anchorage": "alaska",
    "America/Toronto": "montreal|ottawa|quebec",
    "America/Edmonton": "calgary",
    "America/Sao_Paulo": "rio|rio de janeiro|brasilia",
    "Australia/Sydney": "canberra",
    "Pacific/Auckland": "wellington|christchurch",
    "Africa/Johannesburg": "cape town|durban|pretoria",
}
CITY_ALIASES = {
    name: zone for zone, names in _CITY_ALIAS_TABLE.items() for name in names.split("|")
}

# Countries typed in a short or everyday form -> ISO 3166 code.
COUNTRY_ALIASES = {
    **dict.fromkeys(("usa", "us", "america", "united states of america"), "US"),
    **dict.fromkeys(
        ("uk", "britain", "great britain", "united kingdom", "england", "scotland", "wales"),
        "GB",
    ),
    **dict.fromkeys(("korea", "south korea"), "KR"),
    "north korea": "KP",
    "czechia": "CZ",
    **dict.fromkeys(("uae", "emirates"), "AE"),
    **dict.fromkeys(("holland", "the netherlands"), "NL"),
    "ivory coast": "CI",
    **dict.fromkeys(("dr congo", "drc"), "CD"),
    "vatican": "VA",
}

# The zone that stands for a country with several (zone.tab lists the others first).
COUNTRY_ZONES = {
    "AU": "Australia/Sydney",
    "BR": "America/Sao_Paulo",
    "CA": "America/Toronto",
    "RU": "Europe/Moscow",
    "UA": "Europe/Kyiv",
    "UZ": "Asia/Tashkent",
    "FM": "Pacific/Pohnpei",
}

_OFFSET = re.compile(r"(?:utc|gmt)(?:\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?)?")


def city_label(key: str) -> str:
    """Asia/Kuala_Lumpur -> Kuala Lumpur"""
    return key.rsplit("/", 1)[-1].replace("_", " ")


def _norm(text: str) -> str:
    text = text.casefold().replace("’", "'").replace("'", "").replace(".", "")
    return " ".join(text.replace("_", " ").replace("-", " ").split())


class Places:
    """Looks up places by name. Build once; lookups are dictionary reads."""

    def __init__(self, tzdata: Path = TZDATA) -> None:
        available = zoneinfo.available_timezones()
        self._zones: dict[str, tuple[str, str]] = {}  # name -> (zone key, kind)
        self._labels: dict[str, str] = {}  # name -> label

        def add(name: str, key: str, kind: str, label: str) -> None:
            if key in available:
                self._zones.setdefault(name, (key, kind))
                self._labels.setdefault(name, label)

        for name, key in CITY_ALIASES.items():
            add(name, key, "city", name.title() if len(name) > 3 else name.upper())
        canonical = _zone_tab(tzdata)
        # Canonical zones first, so they win a name shared with a link.
        for key in sorted(available, key=lambda k: (k not in canonical, k)):
            if "/" not in key or key.startswith(("Etc/", "SystemV/")):
                continue
            add(_norm(city_label(key)), key, "city", city_label(key))
            add(_norm(key), key, "city", city_label(key))
        by_country: dict[str, list[str]] = {}
        for key, cc in canonical.items():
            by_country.setdefault(cc, []).append(key)
        names = _country_names(tzdata)

        def add_country(name: str, cc: str) -> None:
            if cc not in by_country or cc not in names:
                return
            key = COUNTRY_ZONES.get(cc, by_country[cc][0])
            label = names[cc]
            if len(by_country[cc]) > 1:
                label += f" ({city_label(key)})"
            add(name, key, "country", label)

        # Aliases first: "korea" is South Korea, not whichever "Korea (...)" comes first.
        for name, cc in COUNTRY_ALIASES.items():
            add_country(name, cc)
        for cc, country in names.items():
            add_country(_norm(country), cc)
            add_country(_norm(re.sub(r"\s*\(.*\)", "", country)), cc)

    def find(self, text: str, *, bare: bool = False) -> Place | None:
        """The place a name refers to. bare=True (a name typed on its own, which is
        probably an app search) only accepts full city and country names."""
        name = _norm(text)
        if name.startswith(("in ", "at ")):
            name = name[3:]
        if not name:
            return None
        if bare:
            if len(name) < 4 or "/" in name:
                return None
        else:
            offset = _offset_place("".join(text.casefold().split()).removeprefix("in"))
            if offset is not None:
                return offset
            if name in ABBREVIATIONS:
                key = ABBREVIATIONS[name]
                return Place(city_label(key), zoneinfo.ZoneInfo(key), "abbreviation")
        found = self._zones.get(name)
        if found is None:
            return None
        key, kind = found
        return Place(self._labels[name], zoneinfo.ZoneInfo(key), kind)


def _offset_place(name: str) -> Place | None:
    m = _OFFSET.fullmatch(name)
    if m is None:
        return None
    sign, hours, minutes = m.groups()
    if sign is None:
        return Place("UTC", UTC, "offset")
    delta = timedelta(hours=int(hours), minutes=int(minutes or 0))
    if delta > timedelta(hours=14) or int(minutes or 0) >= 60:
        return None
    label = f"UTC{sign}{int(hours)}" + (f":{minutes}" if minutes and minutes != "00" else "")
    return Place(label, timezone(delta if sign == "+" else -delta), "offset")


def _zone_tab(tzdata: Path) -> dict[str, str]:
    """zone key -> country code, in zone.tab order."""
    zones: dict[str, str] = {}
    try:
        lines = (tzdata / "zone.tab").read_text(encoding="utf-8").splitlines()
    except OSError:
        return zones
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        cc, _coords, key, *_ = line.split("\t")
        zones[key] = cc
    return zones


def _country_names(tzdata: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    try:
        lines = (tzdata / "iso3166.tab").read_text(encoding="utf-8").splitlines()
    except OSError:
        return names
    for line in lines:
        if line.startswith("#") or "\t" not in line:
            continue
        cc, name = line.split("\t", 1)
        names[cc] = name.strip()
    return names


@functools.cache
def places() -> Places:
    return Places()


def system_zone() -> tzinfo:
    """The computer's timezone as a named zone (so its DST rules are known)."""
    candidates = [os.environ.get("TZ", "").lstrip(":")]
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            candidates.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for key in candidates:
        if key:
            try:
                return zoneinfo.ZoneInfo(key)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError):
                continue
    return datetime.now().astimezone().tzinfo or UTC


def zone_label(tz: tzinfo) -> str:
    key = getattr(tz, "key", None)
    return city_label(key) if key else "local time"


# --- queries --------------------------------------------------------------------------

_CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?(am|pm)?")
_CURRENT = re.compile(
    r"(?:what time is it in|whats the time in|what is the time in|current time in"
    r"|time in|time at|now in) (.+)"
)


def parse(text: str, now: datetime, lookup: Places | None = None) -> TimeAnswer | None:
    """What a query asks about times, or None. `now` must be timezone-aware."""
    lookup = lookup or places()
    s = _normalize(text)
    if not s:
        return None
    m = _CURRENT.fullmatch(s) or re.fullmatch(r"(.+?) (?:time|now)", s)
    if m is not None and (place := lookup.find(m.group(1))) is not None:
        return CurrentTime(place, explicit=True)
    s = s.removesuffix(" time")  # "3pm tokyo time"
    # "<moment> to <place>" / "<moment with a place> in <place>"
    for sep in (" to ", " into ", " in "):
        start = len(s)
        while (i := s.rfind(sep, 0, start)) > 0:
            start = i
            target = lookup.find(s[i + len(sep) :])
            if target is None:
                continue
            moment = _moment(s[:i].split(), now, lookup)
            if moment is None or (sep == " in " and moment[1] is None):
                continue
            return Conversion(moment[0], moment[1], target)
    moment = _moment(s.split(), now, lookup)
    if moment is not None and moment[1] is not None:
        return Conversion(moment[0], moment[1], None)
    place = lookup.find(s, bare=True)
    return None if place is None else CurrentTime(place, explicit=False)


def _normalize(text: str) -> str:
    s = text.casefold().strip().replace("’", "'").replace("'", "")
    s = re.sub(r"\b([ap])\.m\.?(?=\s|$)", r"\1m", s)  # 3 p.m. -> 3 pm
    s = re.sub(r"(\d)\s+([ap]m)\b", r"\1\2", s)  # 3 pm -> 3pm
    s = re.sub(r"[?!,]", " ", s)
    return " ".join(s.split())


def _clock(word: str) -> tuple[int, int] | str | None:
    """(hour, minute) for 3pm / 15:00 / 9:30am / noon, "now", or None."""
    if word in ("noon", "midday"):
        return 12, 0
    if word == "midnight":
        return 0, 0
    if word == "now":
        return "now"
    m = _CLOCK.fullmatch(word)
    if m is None:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm is None and m.group(2) is None:
        return None  # a bare number is not a time
    if minute > 59:
        return None
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    elif hour > 23:
        return None
    return hour, minute


def _moment(
    words: list[str], now: datetime, lookup: Places
) -> tuple[datetime, Place | None] | None:
    """Parse "[day] <clock> [day] [place]" in any order into (moment, place or None)."""
    clocks = [(i, c) for i, w in enumerate(words) if (c := _clock(w)) is not None]
    if len(clocks) != 1:
        return None
    index, clock = clocks[0]
    rest = words[:index] + words[index + 1 :]
    if index > 0 and words[index - 1] == "at":
        rest = words[: index - 1] + words[index + 1 :]
    rest = [w for w in rest if w not in ("on",)]
    # Split what is left into a day phrase and a place, either way round.
    for cut in range(len(rest) + 1):
        for day_words, place_words in ((rest[:cut], rest[cut:]), (rest[cut:], rest[:cut])):
            place = lookup.find(" ".join(place_words)) if place_words else None
            if place_words and place is None:
                continue
            tz = place.tz if place is not None else now.tzinfo
            local_now = now.astimezone(tz)
            if clock == "now":
                if day_words:
                    continue
                return local_now, place
            day = local_now.date()
            if day_words:
                parsed = dates.parse_date(" ".join(day_words), day)
                if parsed is None:
                    continue
                day = parsed
            return _at(day, clock, tz), place
    return None


def _at(day: date, clock: tuple[int, int], tz: tzinfo) -> datetime:
    naive = datetime(day.year, day.month, day.day, clock[0], clock[1])
    # A wall time skipped by a DST change is moved on by the gap, as clocks do.
    return naive.replace(tzinfo=tz).astimezone(UTC).astimezone(tz)


# --- formatting -----------------------------------------------------------------------


def format_time(moment: datetime, clock_24h: bool) -> str:
    if clock_24h:
        return f"{moment.hour:02d}:{moment.minute:02d}"
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def format_offset(moment: datetime) -> str:
    """UTC+9, UTC-3:30, UTC"""
    offset = moment.utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds()) // 60
    if minutes == 0:
        return "UTC"
    sign = "+" if minutes > 0 else "-"
    h, m = divmod(abs(minutes), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def difference(there: datetime, here: datetime) -> str:
    """How far `there`'s clock is from `here`'s: "1h ahead", "7h 30m behind", "same time"."""
    delta = (there.utcoffset() or timedelta(0)) - (here.utcoffset() or timedelta(0))
    minutes = int(delta.total_seconds()) // 60
    if minutes == 0:
        return "same time"
    h, m = divmod(abs(minutes), 60)
    amount = f"{h}h" + (f" {m}m" if m else "") if h else f"{m}m"
    return f"{amount} {'ahead' if minutes > 0 else 'behind'}"


def day_shift(moment: datetime, reference: date) -> str:
    """ " (next day)" when a converted time falls on another date than it started."""
    days = (moment.date() - reference).days
    if days == 0:
        return ""
    if days == 1:
        return " (next day)"
    if days == -1:
        return " (previous day)"
    return f" ({days:+d} days)"


def short_day(d: date) -> str:
    """Mon 28 Sep"""
    return f"{dates.WEEKDAY_NAMES[d.weekday()][:3]} {d.day} {dates.MONTH_NAMES[d.month - 1][:3]}"
