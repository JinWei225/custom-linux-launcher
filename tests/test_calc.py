from fractions import Fraction

import pytest

from launcher import calc
from launcher.config import ConfigError, parse_texts
from launcher.engine import Engine
from launcher.providers.base import CONVERSION_SCORE, Result
from launcher.providers.calculator import CalculatorProvider
from launcher.providers.dates import DatesProvider


def value(text):
    answer = calc.parse(text)
    assert answer is not None, f"{text!r} has no answer"
    return calc.format_value(answer.value, grouping=False)


# --- arithmetic ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1+1", "2"),
        ("12*(3+4)", "84"),
        ("2 + 3 * 4", "14"),
        ("(2 + 3) * 4", "20"),
        ("10 / 4", "2.5"),
        ("7 - 10", "-3"),
        ("2 x 3", "6"),
        ("2x3", "6"),
        ("12×3−1", "35"),
        ("9 ÷ 3", "3"),
        ("2**10", "1024"),
        ("10 mod 3", "1"),
        ("-2^2", "-4"),  # the power binds tighter than the minus, as on paper
        ("(-2)^2", "4"),
        ("2^3^2", "512"),  # right-associative
        ("2^-1", "0.5"),
        ("--3+1", "4"),
        ("1,234 * 2", "2468"),
        ("1,234,567.5 + 0.5", "1234568"),
        (".5 + .25", "0.75"),
        ("5. + 1", "6"),
        ("1.5e3 + 1", "1501"),
        ("0xff + 1", "256"),
        ("0b101 * 2", "10"),
        ("0o17 + 1", "16"),
        ("2+2=", "4"),
        ("  3 * 3  ", "9"),
    ],
)
def test_arithmetic(text, expected):
    assert value(text) == expected


def test_exact_decimals_and_big_integers():
    assert value("0.1 + 0.2") == "0.3"
    assert value("1/3 * 3") == "1"
    assert value("2^64") == "18446744073709551616"
    assert value("100!/98!") == "9900"
    assert calc.parse("1/3").value == Fraction(1, 3)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("150 + 10%", "165"),
        ("150 - 10%", "135"),
        ("150 * 10%", "15"),
        ("20% of 80", "16"),
        ("50%", "0.5"),
        ("200 + 20% of 50", "210"),
        ("(100 + 10%) + 10%", "121"),
    ],
)
def test_percentages(text, expected):
    assert value(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("sqrt(16)", "4"),
        ("sqrt 16", "4"),
        ("√16", "4"),
        ("sqrt(1/4)", "0.5"),
        ("sqrt(9)!", "6"),  # brackets end the argument: 3!, not the root of 9!
        ("sqrt 2", "1.4142135623731"),
        ("cbrt(27)", "3"),
        ("cbrt(-8)", "-2"),
        ("(-8)^(1/3)", "-2"),
        ("abs(-3.5)", "3.5"),
        ("round(2.5)", "3"),  # halves go away from zero
        ("round(-2.5)", "-3"),
        ("floor(2.7)", "2"),
        ("ceil(2.1)", "3"),
        ("log 1000", "3"),
        ("log2(8)", "3"),
        ("log2 8", "3"),
        ("ln e", "1"),
        ("exp(0)", "1"),
        ("sin 30°", "0.5"),
        ("sin(30 deg)", "0.5"),
        ("cos(60°)", "0.5"),
        ("tan(45°)", "1"),
        ("sin(pi)", "0"),  # not 1.2e-16
        ("asin(1) * 2", "3.14159265358979"),
        ("5!", "120"),
        ("3!!", "720"),
        ("0!", "1"),
        ("2pi", "6.28318530717959"),
        ("3(4+5)", "27"),
        ("(1+2)(3+4)", "21"),
        ("2 sqrt 9", "6"),
        ("tau / 2", "3.14159265358979"),
        ("π*1", "3.14159265358979"),
    ],
)
def test_functions_constants_and_postfix(text, expected):
    assert value(text) == expected


def test_a_missing_closing_bracket_at_the_end_is_fine_while_typing():
    assert value("(1+2") == "3"
    assert value("sqrt(16") == "4"
    assert value("2*(3+(4") == "14"


def test_starting_with_equals_answers_a_bare_number_or_constant():
    assert value("=pi") == "3.14159265358979"
    assert value("=e") == "2.71828182845905"
    assert value("= 42") == "42"
    assert value("=2026-11-11") == "2004"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "=",
        "42",  # a lone number is a search
        "-5",
        "1e5",
        "3.14",
        "30°",
        "pi",  # so is a lone constant
        "e",
        "x",
        "1 2",
        "windows 11",
        "covid-19",
        "firefox",
        "sqrt",
        "100 usd",  # the currency converter's
        "1.5k * 2",
        "2026-11-11",  # the dates converter's
        "10:30",
        "1.2.3",
        "2+",
        "*3",
        "(1+2))",
        "sqrt(16) )",
        "10% of",
        "of 10",
    ],
)
def test_not_sums(text):
    assert calc.parse(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "1/0",
        "5 mod 0",
        "0^-1",
        "sqrt(-1)",
        "(-8)^0.5",
        "log(0)",
        "ln(-1)",
        "asin(2)",
        "2.5!",
        "(-1)!",
        "1001!",
        "9^9^9",  # far too big: no answer, and quickly
        "10^400 * 10^400 + 0.5 * sin(1)",
        "1e999999999 + 1",
        "(" * 5000 + "1",
        "exp(1000)",
    ],
)
def test_no_answer(text):
    assert calc.parse(text) is None


def test_expression_is_echoed_as_typed():
    assert calc.parse("  150 + 10% ").expression == "150 + 10%"
    assert calc.parse("=pi").expression == "pi"
    assert calc.parse("2+2=").expression == "2+2"


# --- formatting ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "number, shown, copied",
    [
        (Fraction(1234567), "1,234,567", "1234567"),
        (Fraction(-1234), "-1,234", "-1234"),
        (Fraction(12345, 10), "1,234.5", "1234.5"),
        (Fraction(1, 3), "0.333333333333333", "0.333333333333333"),
        (Fraction(0), "0", "0"),
        (0.30000000000000004, "0.3", "0.3"),
        (2.0000000000000004, "2", "2"),
        (Fraction(2**64), "18,446,744,073,709,551,616", "18446744073709551616"),
        (Fraction(10**21), "1e21", "1e21"),
        (Fraction(15, 10**8), "1.5e-7", "1.5e-7"),
        (Fraction(1, 10**6), "0.000001", "0.000001"),
        (1e100, "1e100", "1e100"),
        (-2.5e-10, "-2.5e-10", "-2.5e-10"),
        (123456789.125, "123,456,789.125", "123456789.125"),
    ],
)
def test_format_value(number, shown, copied):
    assert calc.format_value(number) == shown
    assert calc.format_value(number, grouping=False) == copied


def test_as_fraction_only_for_fractions_without_a_short_decimal():
    assert calc.as_fraction(Fraction(1, 3)) == "1/3"
    assert calc.as_fraction(Fraction(-5, 6)) == "-5/6"
    assert calc.as_fraction(Fraction(1, 4)) is None
    assert calc.as_fraction(Fraction(3)) is None
    assert calc.as_fraction(Fraction(1, 1001)) is None
    assert calc.as_fraction(0.5) is None


# --- provider --------------------------------------------------------------------------


def provider(host, toml=""):
    p = CalculatorProvider(host)
    config, _ = parse_texts(toml, None)
    p.configure(config)
    return p


def test_provider_row(host):
    (result,) = provider(host).query("1234 * 2 + 0.5")
    assert (result.title, result.subtitle) == ("2,468.5", "1234 * 2 + 0.5")
    assert result.score == CONVERSION_SCORE and not result.learn
    assert result.icon == "accessories-calculator"


def test_enter_copies_and_alt_enter_pastes_plain_digits(host):
    (result,) = provider(host).query("1000 * 1000")
    result.action()
    result.alt_action()
    assert host.calls == [("copy", "1000000"), ("paste-text", "1000000")]


def test_provider_shows_a_simple_fraction(host):
    (result,) = provider(host).query("2/6 + 1")
    assert result.subtitle == "2/6 + 1 = 4/3"
    (result,) = provider(host).query("1/3")
    assert result.subtitle == "1/3"  # not "1/3 = 1/3"


def test_provider_ignores_other_text(host):
    p = provider(host)
    assert p.query("") == []
    assert p.query("firefox") == []
    assert p.query("1/0") == []


def test_provider_can_be_turned_off(host):
    assert provider(host, "[converters]\ncalculator = false").query("1+1") == []


def test_config_default_and_validation():
    assert parse_texts("", None)[0].converters.calculator is True
    with pytest.raises(ConfigError):
        parse_texts('[converters]\ncalculator = "yes"', None)


class _App:
    def __init__(self, name):
        self.name = name

    def query(self, text):
        if self.name.startswith(text.strip()):
            return [Result(id=f"app:{self.name}", title=self.name, score=1.0)]
        return []


def test_engine_puts_the_answer_first_and_before_a_date(host):
    from datetime import date

    engine = Engine(
        {
            "apps": _App("2+2 game"),
            "calculator": provider(host),
            "dates": DatesProvider(host, today=lambda: date(2026, 11, 11)),
        }
    )
    assert [r.id for r in engine.query("2+2", "all", 8)] == ["calculator", "app:2+2 game"]
    assert [r.id for r in engine.query("3/4", "all", 8)][:2] == ["calculator", "date:iso"]
    assert [r.id for r in engine.query("2026-11-11", "all", 8)][0] == "date:iso"
