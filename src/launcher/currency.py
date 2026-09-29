"""Currency conversion: "100 usd", "1.5k jpy to myr", "(12+30)*3 euros in won".

Rates come from open.er-api.com (ExchangeRate-API's free, keyless endpoint; ~160
currencies, updated once a day). They are downloaded in the background, kept in
~/.cache/launcher/rates.json and refreshed every few hours, so typing never waits on
the network and conversions keep working offline with the last rates.

Pure Python (no GTK) so it can be tested with a fake fetcher.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

RATES_URL = "https://open.er-api.com/v6/latest/USD"
ATTRIBUTION_URL = "https://www.exchangerate-api.com"
TIMEOUT_SECONDS = 10
MAX_BYTES = 256 * 1024
RETRY_ERROR_AFTER = 10 * 60  # network error (offline?): try again after 10 minutes


# --- rates ----------------------------------------------------------------------------


class FetchError(Exception):
    """Temporary failure (network down, server error): try again later."""


@dataclass(frozen=True)
class Rates:
    """Units of each currency per 1 USD."""

    rates: dict[str, float]
    updated: float  # when the provider last updated them (unix time)
    fetched: float  # when we downloaded them

    def convert(self, amount: float, source: str, target: str) -> float:
        return amount / self.rates[source] * self.rates[target]

    def to_json(self) -> str:
        return json.dumps({"updated": self.updated, "fetched": self.fetched, "rates": self.rates})

    @classmethod
    def from_json(cls, text: str) -> Rates:
        data = json.loads(text)
        return cls(_valid_rates(data["rates"]), float(data["updated"]), float(data["fetched"]))


def _valid_rates(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError("rates must be an object")
    rates = {
        code.upper(): float(value)
        for code, value in raw.items()
        if isinstance(code, str)
        and re.fullmatch(r"[A-Za-z]{3}", code)
        and isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    }
    if "USD" not in rates or len(rates) < 2:
        raise ValueError("rates are missing USD or empty")
    return rates


def fetch_rates(now: float | None = None) -> Rates:
    """Download the latest rates. Raises FetchError on any failure."""
    request = urllib.request.Request(RATES_URL, headers={"User-Agent": "launcher-currency/1"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BYTES)
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError(str(e)) from e
    try:
        data = json.loads(body)
        if data.get("result") != "success":
            raise ValueError(data.get("error-type") or "request failed")
        return Rates(
            _valid_rates(data["rates"]),
            float(data.get("time_last_update_unix") or time.time()),
            now if now is not None else time.time(),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise FetchError(f"unexpected answer: {e}") from e


class RatesCache:
    """The last downloaded rates, refreshed in the background when they get old.

    `rates()` never blocks. `on_update` is called (through `call_soon`, i.e. on the main
    loop) when new rates land, so an open window can redraw."""

    def __init__(
        self,
        path: Path,
        fetch: Callable[[], Rates] = fetch_rates,
        on_update: Callable[[], None] | None = None,
        call_soon: Callable[[Callable[[], None]], None] = lambda fn: fn(),
        executor: ThreadPoolExecutor | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._fetch = fetch
        self._on_update = on_update
        self._call_soon = call_soon
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="rates")
        self._now = now
        self._lock = threading.Lock()
        self._in_flight = False
        self._rates: Rates | None = self._load()
        self.refresh_after = 6 * 3600.0
        self.last_error: tuple[float, str] | None = None  # (when, why) of the last failure

    def _load(self) -> Rates | None:
        try:
            return Rates.from_json(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("ignoring unreadable %s: %s", self._path, e)
            return None

    def rates(self, refresh: bool = True) -> Rates | None:
        """The rates we have (possibly old); starts a refresh if they are due one."""
        if refresh:
            self.maybe_refresh()
        return self._rates

    def now(self) -> float:
        return self._now()

    def is_due(self) -> bool:
        """True when there are no rates, or they were downloaded refresh_after ago."""
        return self._rates is None or self._now() - self._rates.fetched >= self.refresh_after

    def maybe_refresh(self) -> None:
        if not self.is_due():
            return
        error = self.last_error
        if error is not None and self._now() - error[0] < RETRY_ERROR_AFTER:
            return
        self.refresh()

    def refresh(self) -> None:
        """Download now (unless a download is already running)."""
        with self._lock:
            if self._in_flight:
                return
            self._in_flight = True
        self._executor.submit(self._fetch_and_store)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _fetch_and_store(self) -> None:
        try:
            rates = self._fetch()
        except Exception as e:
            if not isinstance(e, FetchError):
                log.exception("downloading exchange rates failed")
            else:
                log.info("exchange rates not available: %s", e)
            self.last_error = (self._now(), str(e))
            return
        finally:
            with self._lock:
                self._in_flight = False
        self._rates = rates
        self.last_error = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(rates.to_json(), encoding="utf-8")
            os.replace(tmp, self._path)  # atomic: never a half-written file
        except OSError as e:
            log.error("cannot store exchange rates: %s", e)
        log.info("exchange rates updated (%d currencies)", len(rates.rates))
        if self._on_update is not None:
            self._call_soon(self._on_update)


# --- currencies -----------------------------------------------------------------------

# Everyday names -> code. Plurals ending in "s" are found by dropping the "s".
# Names used by several currencies (peso, krone, dinar) need their country.
NAMES = {
    **dict.fromkeys(("dollar", "buck", "us dollar", "american dollar"), "USD"),
    **dict.fromkeys(("euro",), "EUR"),
    **dict.fromkeys(("pound", "quid", "sterling", "pound sterling", "british pound"), "GBP"),
    **dict.fromkeys(("yen", "japanese yen"), "JPY"),
    **dict.fromkeys(("yuan", "renminbi", "rmb", "chinese yuan"), "CNY"),
    **dict.fromkeys(("won", "korean won", "south korean won"), "KRW"),
    **dict.fromkeys(("ringgit", "malaysian ringgit"), "MYR"),
    **dict.fromkeys(("singapore dollar", "sing dollar"), "SGD"),
    **dict.fromkeys(("hong kong dollar",), "HKD"),
    **dict.fromkeys(("taiwan dollar", "new taiwan dollar", "nt dollar"), "TWD"),
    **dict.fromkeys(("australian dollar", "aussie dollar"), "AUD"),
    **dict.fromkeys(("canadian dollar",), "CAD"),
    **dict.fromkeys(("new zealand dollar", "kiwi dollar"), "NZD"),
    **dict.fromkeys(("baht", "thai baht"), "THB"),
    **dict.fromkeys(("rupiah", "indonesian rupiah"), "IDR"),
    **dict.fromkeys(("rupee", "indian rupee"), "INR"),
    **dict.fromkeys(("dong", "vietnamese dong"), "VND"),
    **dict.fromkeys(("philippine peso",), "PHP"),
    **dict.fromkeys(("mexican peso",), "MXN"),
    **dict.fromkeys(("franc", "swiss franc"), "CHF"),
    **dict.fromkeys(("swedish krona", "krona"), "SEK"),
    **dict.fromkeys(("norwegian krone",), "NOK"),
    **dict.fromkeys(("danish krone",), "DKK"),
    **dict.fromkeys(("rand",), "ZAR"),
    **dict.fromkeys(("real", "reais", "brazilian real"), "BRL"),
    **dict.fromkeys(("ruble", "rouble"), "RUB"),
    **dict.fromkeys(("lira", "turkish lira"), "TRY"),
    **dict.fromkeys(("shekel", "sheqel"), "ILS"),
    **dict.fromkeys(("dirham", "uae dirham"), "AED"),
    **dict.fromkeys(("riyal", "saudi riyal"), "SAR"),
    **dict.fromkeys(("taka",), "BDT"),
    **dict.fromkeys(("zloty",), "PLN"),
    **dict.fromkeys(("forint",), "HUF"),
    **dict.fromkeys(("koruna", "czech koruna"), "CZK"),
    **dict.fromkeys(("brunei dollar",), "BND"),
}

# Codes that are also English words: typed in lower case they only count with a target
# ("10 top" is probably a search; "10 top to usd" is not).
WORD_CODES = {"ALL", "TOP", "CUP", "TRY", "BOB", "MOP", "RUB", "MAD", "PEN", "GEL", "BAM", "SOS"}

# Currencies nobody writes cents for.
NO_DECIMALS = {"JPY", "KRW", "VND", "IDR", "CLP", "ISK", "PYG", "UGX", "HUF", "IRR", "LAK"}

# Country (ISO 3166) -> currency, to guess the home currency from the timezone.
COUNTRY_CURRENCY = {
    "MY": "MYR", "SG": "SGD", "BN": "BND", "ID": "IDR", "TH": "THB", "VN": "VND",
    "PH": "PHP", "MM": "MMK", "KH": "KHR", "LA": "LAK", "CN": "CNY", "HK": "HKD",
    "MO": "MOP", "TW": "TWD", "JP": "JPY", "KR": "KRW", "IN": "INR", "PK": "PKR",
    "BD": "BDT", "LK": "LKR", "NP": "NPR", "AE": "AED", "SA": "SAR", "QA": "QAR",
    "KW": "KWD", "BH": "BHD", "OM": "OMR", "IL": "ILS", "TR": "TRY", "EG": "EGP",
    "ZA": "ZAR", "NG": "NGN", "KE": "KES", "MA": "MAD", "US": "USD", "CA": "CAD",
    "MX": "MXN", "BR": "BRL", "AR": "ARS", "CL": "CLP", "CO": "COP", "PE": "PEN",
    "AU": "AUD", "NZ": "NZD", "GB": "GBP", "CH": "CHF", "NO": "NOK", "SE": "SEK",
    "DK": "DKK", "IS": "ISK", "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON",
    "BG": "BGN", "RU": "RUB", "UA": "UAH", "RS": "RSD",
    **dict.fromkeys(
        ("DE", "FR", "IT", "ES", "PT", "NL", "BE", "LU", "IE", "AT", "FI", "GR", "SK",
         "SI", "EE", "LV", "LT", "MT", "CY", "HR"),
        "EUR",
    ),
}  # fmt: skip


def home_currency(
    configured: str, zone_key: str | None, tzdata: Path = Path("/usr/share/zoneinfo")
) -> str:
    """The configured home currency, or else the one of the country the clock is set to."""
    if configured:
        return configured.upper()
    if zone_key:
        try:
            for line in (tzdata / "zone.tab").read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                if not line.startswith("#") and len(parts) >= 3 and parts[2] == zone_key:
                    return COUNTRY_CURRENCY.get(parts[0], "USD")
        except OSError:
            pass
    return "USD"


# --- queries --------------------------------------------------------------------------


@dataclass(frozen=True)
class CurrencyQuery:
    amount: float
    source: str
    target: str | None  # None: the home currency
    expression: str | None = None  # the arithmetic typed, if more than a plain number


_TARGET_SEP = re.compile(r"\s+(?:to|in|into|as)\s+|\s*(?:->|→|=)\s*")


def parse(text: str, codes: set[str]) -> CurrencyQuery | None:
    """What "100 usd to myr" asks, or None. `codes` are the known currency codes."""
    s = _normalize(text)
    if not s:
        return None
    for m in reversed(list(_TARGET_SEP.finditer(s))):
        target = _currency(s[m.end() :], codes, loose=True)
        if target is None:
            continue
        left = _amount_and_currency(s[: m.start()], text, codes, loose=True)
        if left is not None:
            amount, source, expression = left
            return CurrencyQuery(amount, source, target, expression)
    left = _amount_and_currency(s, text, codes, loose=False)
    if left is None or left[0] is None:
        return None
    amount, source, expression = left
    return CurrencyQuery(amount, source, None, expression)


def _normalize(text: str) -> str:
    s = text.casefold().strip().replace("’", "'")
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)  # 1,234,567.89
    s = re.sub(r"(?<=[\d.)])(?=[a-z]{2,})", " ", s)  # 100usd -> 100 usd
    s = s.replace("×", "*").replace("÷", "/")
    return " ".join(s.split())


def _currency(text: str, codes: set[str], *, loose: bool, original: str = "") -> str | None:
    name = text.strip()
    if re.fullmatch(r"[a-z]{3}", name) and name.upper() in codes:
        code = name.upper()
        # "10 top": a lower-case word code only counts in a "to" query.
        if code in WORD_CODES and not loose and code not in original:
            return None
        return code
    if name in NAMES:
        return NAMES[name]
    if name.endswith("s") and name[:-1] in NAMES:
        return NAMES[name[:-1]]
    return None


def _amount_and_currency(
    s: str, original: str, codes: set[str], *, loose: bool
) -> tuple[float | None, str, str | None] | None:
    """ "1.5k usd" -> (1500, "USD", None); "usd" alone -> (1, "USD") when loose."""
    words = s.split()
    for n in range(min(4, len(words)), 0, -1):
        source = _currency(" ".join(words[-n:]), codes, loose=loose, original=original)
        if source is None:
            continue
        amount_text = " ".join(words[:-n])
        if not amount_text:
            return (1.0 if loose else None), source, None
        amount = evaluate(amount_text)
        if amount is None:
            return None
        plain = re.fullmatch(r"-?\d+(?:\.\d+)?", amount_text.replace(" ", "")) is not None
        return amount, source, None if plain else amount_text
    return None


# --- arithmetic -----------------------------------------------------------------------

_SUFFIXES = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mil": 1e6, "million": 1e6, "b": 1e9,
             "bn": 1e9, "billion": 1e9}  # fmt: skip
_NUMBER_TOKEN = re.compile(r"\s*(\d+(?:\.\d*)?|\.\d+|[a-z]+|[-+*/x()^])")
MAX_AMOUNT = 1e15


def evaluate(text: str) -> float | None:
    """Value of "1.5k", "(12+30)*3", "2 x 19.99", or None. No eval(): a tiny parser."""
    tokens: list[str] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        m = _NUMBER_TOKEN.match(text, pos)
        if m is None:
            return None
        tokens.append(m.group(1))
        pos = m.end()
    if not tokens:
        return None
    parser = _Arithmetic(tokens)
    try:
        value = parser.expr()
    except (ValueError, ZeroDivisionError, OverflowError, IndexError, RecursionError):
        return None  # RecursionError: thousands of nested brackets
    if parser.pos != len(tokens) or not math.isfinite(value) or abs(value) > MAX_AMOUNT:
        return None
    return value


class _Arithmetic:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expr(self) -> float:
        value = self.term()
        while self.peek() in ("+", "-"):
            value = value + self.term() if self.take() == "+" else value - self.term()
        return value

    def term(self) -> float:
        value = self.power()
        while self.peek() in ("*", "x", "/"):
            if self.take() == "/":
                value /= self.power()
            else:
                value *= self.power()
        return value

    def power(self) -> float:
        base = self.unary()
        if self.peek() == "^":
            self.take()
            exponent = self.power()
            if abs(exponent) > 32:
                raise ValueError("exponent too large")
            if base < 0 and exponent != int(exponent):
                raise ValueError("no real root")  # (-8)^0.5 would be a complex number
            return base**exponent
        return base

    def unary(self) -> float:
        if self.peek() in ("-", "+"):
            return -self.unary() if self.take() == "-" else self.unary()
        return self.atom()

    def atom(self) -> float:
        tok = self.take()
        if tok == "(":
            value = self.expr()
            if self.take() != ")":
                raise ValueError("missing )")
        elif re.fullmatch(r"\d+(?:\.\d*)?|\.\d+", tok):
            value = float(tok)
        else:
            raise ValueError(f"unexpected {tok!r}")
        if self.peek() in _SUFFIXES:
            value *= _SUFFIXES[self.take()]
        return value


# --- formatting -----------------------------------------------------------------------


def decimals(value: float, code: str) -> int:
    """2 (0 for yen, won...), but enough to show 3 significant digits of small values."""
    base = 0 if code in NO_DECIMALS else 2
    if value == 0 or abs(value) >= 1:
        return base
    return max(base, min(8, 2 - math.floor(math.log10(abs(value)))))


def format_amount(value: float, code: str, grouping: bool = True) -> str:
    """4723.5 -> "4,723.50" (or "4723.50" without grouping, for copying)."""
    places = decimals(value, code)
    return f"{value:,.{places}f}" if grouping else f"{value:.{places}f}"


def format_number(value: float) -> str:
    """126.0 -> "126"; 1.5 -> "1.5" (a typed amount, shown back)."""
    if value == int(value) and abs(value) < MAX_AMOUNT:
        return f"{int(value):,}"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def format_rate(value: float) -> str:
    """Exchange rate with 4 significant digits after the point: 4.7235, 0.003216."""
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:.{min(10, 3 - math.floor(math.log10(value)))}f}"


def age(updated: float, now: float) -> str:
    """How old rates are, in words: "today", "yesterday", "3 days ago"."""
    days = int((now - updated) // 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"
