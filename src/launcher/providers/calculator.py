"""Calculator answers: "12*(3+4)" -> 84, "150 + 10%" -> 165, "sqrt 2" -> 1.41421356237310.

Enter copies the answer (plain digits, e.g. 1234.5), Alt+Enter pastes it.
"""

from __future__ import annotations

from .. import calc
from ..config import Config
from .base import CONVERSION_SCORE, Host, Result

ICON = "accessories-calculator"


class CalculatorProvider:
    name = "calculator"

    def __init__(self, host: Host) -> None:
        self._host = host
        self._enabled = True

    def configure(self, config: Config) -> None:
        self._enabled = config.converters.calculator

    def query(self, text: str) -> list[Result]:
        if not self._enabled or not text.strip():
            return []
        answer = calc.parse(text)
        if answer is None:
            return []
        try:
            shown = calc.format_value(answer.value)
            copied = calc.format_value(answer.value, grouping=False)
        except (ValueError, OverflowError, ArithmeticError):
            return []
        subtitle = answer.expression
        fraction = calc.as_fraction(answer.value)
        if fraction is not None and fraction != answer.expression.replace(" ", ""):
            subtitle += f" = {fraction}"
        return [
            Result(
                id="calculator",
                title=shown,
                subtitle=subtitle,
                icon=ICON,
                action=lambda: self._host.copy_text(copied),
                alt_action=lambda: self._host.paste_text(copied),
                learn=False,
                score=CONVERSION_SCORE,
            )
        ]
