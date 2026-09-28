"""Dates typed in words: "tomorrow" -> 2026-09-29, "days until christmas" -> 88 days.

Enter copies the answer, Alt+Enter pastes it into the window the launcher came from.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from .. import dates
from ..config import Config
from .base import CONVERSION_SCORE, Host, Result

ICON = "x-office-calendar"


class DatesProvider:
    name = "dates"

    def __init__(self, host: Host, today: Callable[[], date] = date.today) -> None:
        self._host = host
        self._today = today
        self._enabled = True

    def configure(self, config: Config) -> None:
        self._enabled = config.converters.dates

    def query(self, text: str) -> list[Result]:
        if not self._enabled or not text.strip():
            return []
        today = self._today()
        answer = dates.parse(text, today)
        if isinstance(answer, dates.DateAnswer):
            return self._date_results(answer.date, today)
        if isinstance(answer, dates.DaysAnswer):
            return [self._days_result(answer, today)]
        return []

    def _date_results(self, d: date, today: date) -> list[Result]:
        iso, long = d.isoformat(), dates.long_form(d)
        return [
            self._result("date:iso", iso, f"{long} · {dates.relative(d, today)}", iso, 0),
            self._result("date:long", long, "Long form", long, 1),
        ]

    def _days_result(self, answer: dates.DaysAnswer, today: date) -> Result:
        n = answer.days
        span = f"{dates.short_form(answer.start)} → {dates.short_form(answer.end)}"
        details = [span]
        if abs(n) >= 7:
            details.insert(0, dates.weeks_and_days(n))
        if n < 0:
            if answer.start == today:  # "days until" a day that has gone
                details.append("already past")
            elif answer.end == today:  # "days since" a day still to come
                details.append("still to come")
            else:
                details.append("the second date is earlier")
        title = dates.plural(abs(n), "day")
        return self._result("date:days", title, " · ".join(details), str(abs(n)), 0)

    def _result(self, id: str, title: str, subtitle: str, value: str, rank: int) -> Result:
        return Result(
            id=id,
            title=title,
            subtitle=subtitle,
            icon=ICON,
            action=lambda: self._host.copy_text(value),
            alt_action=lambda: self._host.paste_text(value),
            learn=False,  # answers change with the date; nothing to learn
            score=CONVERSION_SCORE - rank * 0.01,  # keep the rows in this order
        )
