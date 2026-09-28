"""Times across timezones: "time in tokyo", "3pm tokyo" (-> local), "3pm pst to london".

Enter copies the time, Alt+Enter pastes it. A place name typed on its own ("tokyo")
still shows its time, but below every real app, link or snippet match.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, tzinfo

from .. import timezones as tz
from ..config import Config
from .base import CONVERSION_SCORE, Host, Result

ICON = "preferences-system-time"
# "tokyo" on its own: below any fuzzy match (those start at 0.3), above web fallbacks.
PLACE_ONLY_SCORE = 0.2


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TimezonesProvider:
    name = "timezones"

    def __init__(
        self,
        host: Host,
        clock_24h: Callable[[], bool] = lambda: True,
        now: Callable[[], datetime] = _utc_now,
        local_zone: Callable[[], tzinfo] = tz.system_zone,
    ) -> None:
        self._host = host
        self._clock_24h = clock_24h
        self._now = now
        self._local_zone = local_zone
        self._enabled = True

    def configure(self, config: Config) -> None:
        self._enabled = config.converters.timezones
        if self._enabled:
            tz.places()  # build the place index now rather than on a keystroke

    def query(self, text: str) -> list[Result]:
        if not self._enabled or not text.strip():
            return []
        local = self._local_zone()
        now = self._now().astimezone(local)
        answer = tz.parse(text, now)
        if isinstance(answer, tz.CurrentTime):
            return [self._current(answer, now)]
        if isinstance(answer, tz.Conversion):
            return [self._conversion(answer, local)]
        return []

    def _current(self, answer: tz.CurrentTime, now: datetime) -> Result:
        there = now.astimezone(answer.place.tz)
        details = [
            answer.place.name_at(there),
            tz.short_day(there.date()),
            _versus_you(tz.difference(there, now)),
        ]
        if answer.place.kind != "offset":  # "UTC+9" already says it
            details.append(tz.format_offset(there))
        score = CONVERSION_SCORE if answer.explicit else PLACE_ONLY_SCORE
        return self._result(self._time(there), " · ".join(details), score)

    def _conversion(self, answer: tz.Conversion, local: tzinfo) -> Result:
        moment = answer.moment
        there = moment.astimezone(answer.target.tz if answer.target else local)
        source = answer.source.name_at(moment) if answer.source else "here"
        target = answer.target.name_at(there) if answer.target else tz.zone_label(local)
        title = self._time(there) + tz.day_shift(there, moment.date())
        details = [
            f"{target}, {tz.short_day(there.date())}",
            f"from {self._time(moment)} {source}, {tz.difference(moment, there)}",
        ]
        return self._result(title, " · ".join(details), CONVERSION_SCORE, self._time(there))

    def _time(self, moment: datetime) -> str:
        return tz.format_time(moment, self._clock_24h())

    def _result(self, title: str, subtitle: str, score: float, value: str | None = None) -> Result:
        value = value or title
        return Result(
            id="timezone",
            title=title,
            subtitle=subtitle,
            icon=ICON,
            action=lambda: self._host.copy_text(value),
            alt_action=lambda: self._host.paste_text(value),
            learn=False,
            score=score,
        )


def _versus_you(difference: str) -> str:
    """ "1h ahead" -> "1h ahead of you", "2h behind" -> "2h behind you"."""
    if difference.endswith("ahead"):
        return difference + " of you"
    if difference.endswith("behind"):
        return difference + " you"
    return "same time as you"
