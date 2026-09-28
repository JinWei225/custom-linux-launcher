"""Natural-language dates: "tomorrow", "2 months after today", "days until christmas".

Pure Python, English only. Everything is relative to a `today` passed in, so tests can
pin the date. Where English is ambiguous, these rules apply:

  friday, next friday   the first Friday after today (never today itself)
  this friday           the Friday of the current week (Monday to Sunday)
  last friday           the most recent Friday before today
  next week/month/year  today + 1 week / month / year
  25/12, 1/2/2027       day first
  dec 25, christmas     the next one; today counts
  jan 31 + 1 month      Feb 28: adding months stops at the end of the month

Weekday abbreviations ("fri") only count after next/last/this, so typing "sun" or
"wed" in the launcher still searches apps instead of turning into a date.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Duration:
    years: int = 0
    months: int = 0
    days: int = 0  # weeks are folded into days

    def __add__(self, other: Duration) -> Duration:
        return Duration(
            self.years + other.years, self.months + other.months, self.days + other.days
        )


def add(d: date, duration: Duration, sign: int = 1) -> date:
    """d + duration (or - with sign=-1). Months first, clamped to the month's last day."""
    months = d.month - 1 + sign * (duration.years * 12 + duration.months)
    year, month = d.year + months // 12, months % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) + timedelta(days=sign * duration.days)


@dataclass(frozen=True)
class DateAnswer:
    date: date


@dataclass(frozen=True)
class DaysAnswer:
    """The span from start to end ("days until christmas" starts today)."""

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days


Answer = DateAnswer | DaysAnswer


def parse(text: str, today: date) -> Answer | None:
    """The date (or day count) a phrase describes, or None if it isn't one."""
    tokens = _tokenize(text)
    if not tokens:
        return None
    parser = _Parser(tokens, today)
    for rule in (parser.difference, parser.single):
        parser.pos = 0
        parser.past = False
        try:
            answer = rule()
        except (ValueError, OverflowError):  # e.g. "feb 30", or a year past 9999
            answer = None
        if answer is not None and parser.done():
            return answer
    return None


def parse_date(text: str, today: date) -> date | None:
    """Like parse(), for a phrase that names one day ("tomorrow", "next friday")."""
    answer = parse(text, today)
    return answer.date if isinstance(answer, DateAnswer) else None


# --- formatting -----------------------------------------------------------------------

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def long_form(d: date) -> str:
    """Saturday, 28 November 2026"""
    return f"{WEEKDAY_NAMES[d.weekday()]}, {d.day} {MONTH_NAMES[d.month - 1]} {d.year}"


def short_form(d: date) -> str:
    """Sat 28 Nov 2026"""
    return f"{WEEKDAY_NAMES[d.weekday()][:3]} {d.day} {MONTH_NAMES[d.month - 1][:3]} {d.year}"


def relative(d: date, today: date) -> str:
    """today / tomorrow / in 61 days / 3 days ago"""
    days = (d - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    return f"in {days} days" if days > 0 else f"{-days} days ago"


def plural(n: int, unit: str) -> str:
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def weeks_and_days(days: int) -> str:
    """88 -> "12 weeks 4 days"; 14 -> "2 weeks"."""
    weeks, rest = divmod(abs(days), 7)
    parts = [plural(weeks, "week")] if weeks else []
    if rest or not weeks:
        parts.append(plural(rest, "day"))
    return " ".join(parts)


# --- tokens ---------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}"  # 2026-12-25
    r"|\d{1,2}/\d{1,2}(?:/\d{2}(?:\d{2})?)?"  # 25/12, 25/12/26, 25/12/2026
    r"|\d+(?:st|nd|rd|th)?"
    r"|[a-z]+"
    r"|[+-]"
)
# Leading words that only make it a question: "what date is 2 weeks from now".
_FILLER = {"what", "whats", "when", "is", "was", "will", "be", "date", "it"}
_DROPPED = {"the", "on"}


def _tokenize(text: str) -> list[str] | None:
    s = text.casefold().strip().replace("’", "'")
    s = re.sub(r"'s\b", "", s).replace("'", "")  # new year's eve -> new year eve
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)  # twenty-one, day-after-tomorrow
    s = re.sub(r"[,?!]|\.(?!\d)", " ", s)
    tokens: list[str] = []
    pos = 0
    for m in _TOKEN.finditer(s):
        if s[pos : m.start()].strip():
            return None  # characters no rule understands
        tokens.append(m.group())
        pos = m.end()
    if s[pos:].strip():
        return None
    tokens = [t for t in tokens if t not in _DROPPED]
    while len(tokens) > 1 and tokens[0] in _FILLER:
        tokens.pop(0)
    return tokens


_SMALL_NUMBERS = {
    word: n
    for n, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen".split()
    )
}
_TENS = {
    word: 10 * n
    for n, word in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)
}
_UNITS = {
    **dict.fromkeys(("day", "days", "d"), Duration(days=1)),
    **dict.fromkeys(("week", "weeks", "w", "wk", "wks"), Duration(days=7)),
    **dict.fromkeys(("fortnight", "fortnights"), Duration(days=14)),
    **dict.fromkeys(("month", "months", "mo", "mos", "mth", "mths"), Duration(months=1)),
    **dict.fromkeys(("year", "years", "y", "yr", "yrs"), Duration(years=1)),
}
_WEEKDAYS = {name.casefold(): i for i, name in enumerate(WEEKDAY_NAMES)}
_WEEKDAY_ABBREVIATIONS = {
    **_WEEKDAYS,
    **{"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3},
    **{"fri": 4, "sat": 5, "sun": 6},
}
_MONTHS = {
    **{name.casefold(): i for i, name in enumerate(MONTH_NAMES, 1)},
    **{name[:3].casefold(): i for i, name in enumerate(MONTH_NAMES, 1)},
    "sept": 9,
}
_ORDINALS = {
    **{"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5},
    **{"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5},
}
# Fixed-date holidays: (month, day). Moving and lunar holidays are left out on purpose.
HOLIDAYS = {
    ("new", "year"): (1, 1),
    ("new", "years"): (1, 1),
    ("new", "year", "day"): (1, 1),
    ("new", "years", "day"): (1, 1),
    ("valentine",): (2, 14),
    ("valentines",): (2, 14),
    ("valentine", "day"): (2, 14),
    ("valentines", "day"): (2, 14),
    ("april", "fools"): (4, 1),
    ("april", "fools", "day"): (4, 1),
    ("april", "fool", "day"): (4, 1),
    ("halloween",): (10, 31),
    ("christmas", "eve"): (12, 24),
    ("xmas", "eve"): (12, 24),
    ("christmas",): (12, 25),
    ("christmas", "day"): (12, 25),
    ("xmas",): (12, 25),
    ("boxing", "day"): (12, 26),
    ("new", "year", "eve"): (12, 31),
    ("new", "years", "eve"): (12, 31),
}
_LONGEST_HOLIDAY = max(len(k) for k in HOLIDAYS)


class _Parser:
    """Recursive descent over the tokens. Each rule returns None (and leaves pos where
    it was) when it does not match."""

    def __init__(self, tokens: list[str], today: date) -> None:
        self.tokens = tokens
        self.today = today
        self.pos = 0
        # Resolve "friday", "dec 25" and "june" to the last one before today, not the next.
        self.past = False

    # --- helpers

    def done(self) -> bool:
        return self.pos == len(self.tokens)

    def peek(self, offset: int = 0) -> str | None:
        i = self.pos + offset
        return self.tokens[i] if i < len(self.tokens) else None

    def accept(self, *words: str) -> str | None:
        tok = self.peek()
        if tok is not None and tok in words:
            self.pos += 1
            return tok
        return None

    def accept_seq(self, *words: str) -> bool:
        if tuple(self.tokens[self.pos : self.pos + len(words)]) == words:
            self.pos += len(words)
            return True
        return False

    def attempt(self, rule, *args):
        """Run a rule; on no match, rewind so the next rule starts from the same spot."""
        start = self.pos
        value = rule(*args)
        if value is None:
            self.pos = start
        return value

    # --- top level

    def difference(self) -> DaysAnswer | None:
        self.accept_seq("how", "many")
        counted = self.accept("days", "weeks") is not None
        if self.accept("until", "till", "til", "to", "before"):
            end = self.date_expr()
            return None if end is None else DaysAnswer(self.today, end)
        if self.accept("since"):
            self.past = True  # "days since friday": the friday before today
            start = self.date_expr()
            return None if start is None else DaysAnswer(start, self.today)
        if self.accept("between") or (counted and self.accept("from")):
            start = self.date_expr()
            if start is None or not self.accept("and", "to", "until", "till"):
                return None
            end = self.date_expr()
            return None if end is None else DaysAnswer(start, end)
        if counted or self.pos:
            return None
        start = self.date_expr()
        if start is None or not self.accept("to", "until", "till"):
            return None
        end = self.date_expr()
        return None if end is None else DaysAnswer(start, end)

    def single(self) -> DateAnswer | None:
        d = self.date_expr()
        return None if d is None else DateAnswer(d)

    def date_expr(self) -> date | None:
        """A day, possibly shifted: "in 3 weeks", "2 months after today", "xmas - 3d"."""
        start = self.pos
        if self.accept("in"):
            if (duration := self.attempt(self.duration)) is not None:
                return add(self.today, duration)
            self.pos = start
        if (duration := self.attempt(self.duration)) is not None:
            if self.accept("from", "after"):
                base = self.date_expr()
                return None if base is None else add(base, duration)
            if self.accept("before"):
                base = self.date_expr()
                return None if base is None else add(base, duration, -1)
            if self.accept("ago", "earlier", "back"):
                return add(self.today, duration, -1)
            if self.accept("later", "hence"):
                return add(self.today, duration)
            self.pos = start
        base = self.atom()
        if base is None:
            return None
        while True:
            before = self.pos
            sign = {"+": 1, "plus": 1, "-": -1, "minus": -1}.get(self.peek() or "")
            if sign is None:
                break
            self.pos += 1
            duration = self.attempt(self.duration)
            if duration is None:
                self.pos = before
                break
            base = add(base, duration, sign)
        return base

    # --- durations

    def duration(self) -> Duration | None:
        total = self.term()
        if total is None:
            return None
        while True:
            before = self.pos
            self.accept("and")
            term = self.term()
            if term is None:
                self.pos = before
                return total
            total += term

    def term(self) -> Duration | None:
        """3 days / two weeks / a month / a couple of years"""
        start = self.pos
        if self.accept_seq("a", "couple", "of") or self.accept_seq("couple", "of"):
            n = 2
        elif self.accept("a", "an"):
            n = 1
        else:
            n = self.number()
        unit = self.accept(*_UNITS)
        if n is None or unit is None:
            self.pos = start
            return None
        u = _UNITS[unit]
        return Duration(u.years * n, u.months * n, u.days * n)

    def number(self) -> int | None:
        tok = self.peek()
        if tok is None:
            return None
        if tok.isdigit():
            self.pos += 1
            return int(tok)
        if tok in _SMALL_NUMBERS:
            self.pos += 1
            return _SMALL_NUMBERS[tok]
        if tok in _TENS:
            self.pos += 1
            n = _TENS[tok]
            ones = self.peek()
            if ones is not None and 1 <= _SMALL_NUMBERS.get(ones, 0) <= 9:
                self.pos += 1
                n += _SMALL_NUMBERS[ones]
            return n
        return None

    # --- single days

    def atom(self) -> date | None:
        for rule in (
            self.named_day,
            self.nth_weekday,  # before weekday: "last friday of october"
            self.weekday,
            self.period,
            self.boundary,
            self.holiday,
            self.calendar_date,
        ):
            if (d := self.attempt(rule)) is not None:
                return d
        return None

    def named_day(self) -> date | None:
        today = self.today
        if self.accept_seq("day", "after", "tomorrow") or self.accept("overmorrow"):
            return today + timedelta(days=2)
        if self.accept_seq("day", "before", "yesterday"):
            return today - timedelta(days=2)
        if self.accept("today", "now", "tonight", "tdy"):
            return today
        if self.accept("tomorrow", "tmr", "tmrw", "tomorow", "tommorow", "tommorrow"):
            return today + timedelta(days=1)
        if self.accept("yesterday", "ytd"):
            return today - timedelta(days=1)
        return None

    def weekday(self) -> date | None:
        which = self.accept("next", "coming", "this", "last", "previous", "past")
        names = _WEEKDAY_ABBREVIATIONS if which else _WEEKDAYS
        tok = self.accept(*names)
        if tok is None:
            return None
        wd, today = names[tok], self.today
        if which == "this":
            return today + timedelta(days=wd - today.weekday())
        if which in ("last", "previous", "past") or (which is None and self.past):
            return today - timedelta(days=(today.weekday() - wd - 1) % 7 + 1)
        return today + timedelta(days=(wd - today.weekday() - 1) % 7 + 1)

    def period(self) -> date | None:
        """next week / last month / next year (today +- one of them)"""
        sign = {"next": 1, "coming": 1, "this": 0, "last": -1, "previous": -1}.get(
            self.peek() or ""
        )
        if sign is None:
            return None
        self.pos += 1
        unit = self.accept("week", "month", "year")
        if unit is None:
            return None
        return add(self.today, _UNITS[unit], sign)

    def boundary(self) -> date | None:
        """start of next month / end of the year / end of february"""
        edge = self.accept("start", "beginning", "end")
        if edge is None or not self.accept("of"):
            return None
        at_end = edge == "end"
        month = self.attempt(self.month_spec)
        if month is not None:
            year, m = month
            return date(year, m, calendar.monthrange(year, m)[1] if at_end else 1)
        sign = {"next": 1, "this": 0, "last": -1, "previous": -1}.get(self.peek() or "", 0)
        if self.peek() in ("next", "this", "last", "previous"):
            self.pos += 1
        unit = self.accept("week", "month", "year")
        if unit is None:
            return None
        d = add(self.today, _UNITS[unit], sign)
        if unit == "week":
            return d + timedelta(days=(6 if at_end else 0) - d.weekday())
        if unit == "month":
            return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1] if at_end else 1)
        return date(d.year, 12, 31) if at_end else date(d.year, 1, 1)

    def nth_weekday(self) -> date | None:
        """first monday of october / last friday of next month / 2nd tue in march 2027"""
        tok = self.peek()
        if tok not in _ORDINALS and tok != "last":
            return None
        self.pos += 1
        wd_tok = self.accept(*_WEEKDAY_ABBREVIATIONS)
        if wd_tok is None or not self.accept("of", "in"):
            return None
        month = self.month_spec()
        if month is None:
            return None
        year, m = month
        wd = _WEEKDAY_ABBREVIATIONS[wd_tok]
        last_day = calendar.monthrange(year, m)[1]
        matches = [d for d in range(1, last_day + 1) if date(year, m, d).weekday() == wd]
        if tok == "last":
            return date(year, m, matches[-1])
        n = _ORDINALS[tok]
        return date(year, m, matches[n - 1]) if n <= len(matches) else None

    def month_spec(self) -> tuple[int, int] | None:
        """(year, month) for "october", "october 2027", "this/next/last month".

        A month name without a year means the next time it comes round (this month
        counts), so in September "june" is next June."""
        which = self.accept("this", "next", "last", "previous")
        if which is not None:
            if not self.accept("month"):
                return None
            d = add(self.today, Duration(months=1), {"this": 0, "next": 1}.get(which, -1))
            return d.year, d.month
        tok = self.accept(*_MONTHS)
        if tok is None:
            return None
        m = _MONTHS[tok]
        year = self.year()
        if year is None:
            if self.past:
                year = self.today.year - (m > self.today.month)
            else:
                year = self.today.year + (m < self.today.month)
        return year, m

    def year(self) -> int | None:
        tok = self.peek()
        if tok is not None and tok.isdigit() and len(tok) == 4:
            self.pos += 1
            return int(tok)
        return None

    def holiday(self) -> date | None:
        for length in range(_LONGEST_HOLIDAY, 0, -1):
            key = tuple(self.tokens[self.pos : self.pos + length])
            if len(key) == length and key in HOLIDAYS:
                self.pos += length
                return self.upcoming(*HOLIDAYS[key], self.year())
        return None

    def calendar_date(self) -> date | None:
        tok = self.peek()
        if tok is None:
            return None
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", tok):
            self.pos += 1
            return date(*map(int, tok.split("-")))
        if "/" in tok:
            self.pos += 1
            parts = [int(p) for p in tok.split("/")]
            year = None
            if len(parts) == 3:
                year = parts[2] + 2000 if parts[2] < 100 else parts[2]
            return self.upcoming(parts[1], parts[0], year)
        # 25 december [2026] / 25th of dec
        day = self.day_of_month()
        if day is not None:
            self.accept("of")
            month = self.accept(*_MONTHS)
            if month is None:
                return None
            return self.upcoming(_MONTHS[month], day, self.year())
        # december 25 [2026]
        month = self.accept(*_MONTHS)
        if month is None:
            return None
        day = self.day_of_month()
        if day is None:
            return None
        return self.upcoming(_MONTHS[month], day, self.year())

    def day_of_month(self) -> int | None:
        tok = self.peek()
        if tok is None:
            return None
        m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?", tok)
        if m is None or not 1 <= int(m.group(1)) <= 31:
            return None
        self.pos += 1
        return int(m.group(1))

    def upcoming(self, month: int, day: int, year: int | None) -> date:
        """That day in the given year, or else the next time it comes round (today
        counts). In a "since" phrase: the last time it came round instead."""
        if year is not None:
            return date(year, month, day)
        step = -1 if self.past else 1
        year = self.today.year
        while True:  # Feb 29 may be up to 8 years away (2096 -> 2104)
            try:
                d = date(year, month, day)
            except ValueError:
                if (month, day) != (2, 29) or abs(year - self.today.year) > 8:
                    raise
            else:
                if (d <= self.today) if self.past else (d >= self.today):
                    return d
            year += step
