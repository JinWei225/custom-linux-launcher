"""Currency answers: "100 usd" (-> home currency), "1.5k jpy to myr".

Enter copies the amount (plain digits, e.g. 472.35), Alt+Enter pastes it. Rates come
from the RatesCache, which never blocks; until the first download finishes a row says
so instead.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import currency
from ..config import Config
from .base import CONVERSION_SCORE, Host, Result

ICON = "accessories-calculator"
# Known before any rates have been downloaded, so "100 usd" still gets a status row.
_OFFLINE_CODES = set(currency.NAMES.values()) | set(currency.COUNTRY_CURRENCY.values())


class CurrencyProvider:
    name = "currency"

    def __init__(
        self,
        host: Host,
        rates: currency.RatesCache,
        zone_key: Callable[[], str | None] = lambda: None,
    ) -> None:
        self._host = host
        self._cache = rates
        self._zone_key = zone_key
        self._enabled = True
        self._home_setting = ""

    def configure(self, config: Config) -> None:
        converters = config.converters
        self._enabled = converters.currency
        self._home_setting = converters.home_currency
        self._cache.refresh_after = converters.refresh_hours * 3600
        if self._enabled:
            self._cache.maybe_refresh()

    def home(self) -> str:
        return currency.home_currency(self._home_setting, self._zone_key())

    def query(self, text: str) -> list[Result]:
        if not self._enabled or not text.strip():
            return []
        rates = self._cache.rates(refresh=False)
        q = currency.parse(text, set(rates.rates) if rates else _OFFLINE_CODES)
        if q is None:
            return []
        self._cache.maybe_refresh()
        home = self.home()
        target = q.target or home
        if target == q.source and q.target is None:
            target = "USD" if home != "USD" else "EUR"  # "100 myr" at home: show dollars
        if rates is None:
            return [self._status(q, target)]
        missing = [c for c in (q.source, target) if c not in rates.rates]
        if missing:
            return [self._message(f"No exchange rate for {missing[0]}", "")]
        value = rates.convert(q.amount, q.source, target)
        amount = currency.format_number(q.amount)
        typed = f"{q.expression} = {amount}" if q.expression else amount
        rate = currency.format_rate(rates.convert(1, q.source, target))
        details = [
            f"{typed} {q.source} → {target}",
            f"1 {q.source} = {rate} {target}",
            f"rates from {currency.age(rates.updated, self._cache.now())}",
        ]
        if self._cache.last_error is not None and self._cache.is_due():
            details[-1] += " (can't update: offline?)"
        copied = currency.format_amount(value, target, grouping=False)
        return [
            Result(
                id="currency",
                title=f"{currency.format_amount(value, target)} {target}",
                subtitle=" · ".join(details),
                icon=ICON,
                action=lambda: self._host.copy_text(copied),
                alt_action=lambda: self._host.paste_text(copied),
                learn=False,
                score=CONVERSION_SCORE,
            )
        ]

    def _status(self, q: currency.CurrencyQuery, target: str) -> Result:
        error = self._cache.last_error
        if error is None:
            detail = "Downloading them from open.er-api.com…"
        else:
            detail = f"Download failed ({error[1]}); trying again in a few minutes"
        return self._message(f"{q.source} → {target}: no exchange rates yet", detail)

    def _message(self, title: str, subtitle: str) -> Result:
        return Result(
            id="currency",
            title=title,
            subtitle=subtitle,
            icon=ICON,
            learn=False,
            score=CONVERSION_SCORE,
        )
