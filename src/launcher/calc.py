"""Calculator: "12*(3+4)", "150 + 10%", "20% of 80", "sqrt 2", "sin 30°", "2^64", "5!".

A small recursive-descent parser, never eval(). Numbers are exact fractions while only
+ - * / ^ (whole powers) % and ! are used, so 0.1 + 0.2 is 0.3 and 2^64 keeps every
digit; functions and fractional powers switch to floats.

Text is only treated as a sum when it has an operator, a function or brackets: a lone
number or a word like "e" is a search, not a calculation. Starting with "=" lifts that
rule ("=pi").

Pure Python (no GTK) so it can be tested directly.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

Number = Fraction | float

# Exact arithmetic stays exact up to about this many bits; bigger powers become floats
# (and overflow into "no answer" past ~1e308), so "9^9^9" can't hang the launcher.
MAX_EXACT_BITS = 4096
MAX_FACTORIAL = 1000
MAX_LITERAL_EXPONENT = 400  # 1e400 is fine, 1e999999999 is not
SIGNIFICANT_DIGITS = 15  # a float has ~16; one fewer hides 0.30000000000000004
PLAIN_RANGE = (Decimal("1e-6"), Decimal("1e21"))  # shown without an exponent in between

_TOKEN = re.compile(
    r"\s*("
    r"0x[0-9a-f]+|0b[01]+|0o[0-7]+"  # hex, binary, octal
    r"|(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?"  # 12, 1.5, .5, 2e10, 1.5e-3
    r"|\*\*|[-+*/^%!()°√π×÷−]"
    r"|log2(?!\d)|[a-z]+"
    r")"
)

_ISO_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")

_CONSTANTS: dict[str, float] = {"pi": math.pi, "π": math.pi, "e": math.e, "tau": math.tau}


def _snap(x: float) -> float:
    """sin(pi) is 1.2e-16 in floats: call anything that small zero."""
    return 0.0 if abs(x) < 1e-15 else x


def _log(x: Number, f) -> float:
    if x <= 0:
        raise ValueError("log of a number <= 0")
    return f(x)


_FUNCTIONS = {
    "sqrt": lambda x: _root(x, 2),
    "√": lambda x: _root(x, 2),
    "cbrt": lambda x: _root(x, 3),
    "abs": abs,
    "round": lambda x: _round(x),
    "floor": lambda x: Fraction(math.floor(x)),
    "ceil": lambda x: Fraction(math.ceil(x)),
    "ln": lambda x: _log(x, math.log),
    "log": lambda x: _log(x, math.log10),
    "lg": lambda x: _log(x, math.log10),
    "log2": lambda x: _log(x, math.log2),
    "exp": lambda x: math.exp(x),
    "sin": lambda x: _snap(math.sin(x)),
    "cos": lambda x: _snap(math.cos(x)),
    "tan": lambda x: _snap(math.tan(x)),
    "asin": lambda x: math.asin(x),
    "acos": lambda x: math.acos(x),
    "atan": lambda x: math.atan(x),
    "sinh": lambda x: math.sinh(x),
    "cosh": lambda x: math.cosh(x),
    "tanh": lambda x: math.tanh(x),
}


def _round(x: Number) -> Fraction:
    """Halves round away from zero (Python's round() would make 2.5 into 2)."""
    n = math.floor(abs(x) + Fraction(1, 2) if isinstance(x, Fraction) else abs(x) + 0.5)
    return Fraction(n if x >= 0 else -n)


def _root(x: Number, n: int) -> Number:
    if isinstance(x, Fraction) and x >= 0:
        # Exact when both parts are perfect powers: sqrt(16) = 4, sqrt(1/4) = 1/2.
        num, den = _int_root(x.numerator, n), _int_root(x.denominator, n)
        if num is not None and den is not None:
            return Fraction(num, den)
    if x < 0:
        if n % 2 == 0:
            raise ValueError("square root of a negative number")
        return -((-float(x)) ** (1 / n))
    return float(x) ** (1 / n)


def _int_root(value: int, n: int) -> int | None:
    if value.bit_length() > MAX_EXACT_BITS:
        return None
    guess = round(value ** (1 / n)) if value < 2**1000 else None
    if guess is None:
        return None
    for r in (guess - 1, guess, guess + 1):
        if r >= 0 and r**n == value:
            return r
    return None


@dataclass(frozen=True)
class Answer:
    value: Number
    expression: str  # what was typed, tidied up: "150 + 10%"


def parse(text: str) -> Answer | None:
    """The value of the sum typed, or None when it isn't one (or has no answer)."""
    s = text.strip()
    forced = s.startswith("=")
    s = s.lstrip("=").rstrip("=").strip()  # "=pi", "2+2="
    if not s or (not forced and _ISO_DATE.fullmatch(s)):
        return None  # "2026-11-11" is a date (the dates converter answers it), not 2004
    tokens = _tokenize(_normalize(s))
    if tokens is None:
        return None
    parser = _Parser(tokens)
    try:
        value, _percent = parser.expr()
    except (ValueError, ZeroDivisionError, OverflowError, IndexError, RecursionError, TypeError):
        return None
    if parser.pos != len(tokens) or not (forced or parser.operated):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return Answer(value, s)


def _normalize(s: str) -> str:
    s = s.casefold().replace("−", "-").replace("×", "*").replace("÷", "/").replace("**", "^")
    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)  # 1,234,567.89


def _tokenize(s: str) -> list[str] | None:
    tokens: list[str] = []
    pos = 0
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if m is None:
            if s[pos:].strip() == "":
                break
            return None
        tokens.append(m.group(1))
        pos = m.end()
    return tokens or None


def _is_number(tok: str | None) -> bool:
    return tok is not None and (tok[0].isdigit() or tok[0] == ".")


class _Parser:
    """expr := term (+|- term)*      term := unary (*|/|x|mod|implicit unary)*
    unary := (-|+) unary | power      power := postfix (^ unary)?
    postfix := atom (! | % [of unary] | ° | deg)*
    atom := number | constant | ( expr ) | function (( expr ) | unary)

    Each level returns (value, is_percent); `150 + 10%` adds 10% of 150."""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.operated = False  # saw an operator, function or bracket: it's a sum

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expr(self) -> tuple[Number, bool]:
        value, percent = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            self.operated = True
            right, right_percent = self.term()
            if right_percent:  # 150 + 10% = 165, 150 - 10% = 135
                right = value * right
            value = value + right if op == "+" else value - right
            percent = False
        return value, percent

    def term(self) -> tuple[Number, bool]:
        value, percent = self.unary()
        while True:
            tok = self.peek()
            if tok in ("*", "x", "/", "mod"):
                self.take()
                right, _ = self.unary()
                if tok == "/":
                    value = value / right
                elif tok == "mod":
                    value = value % right
                else:
                    value = value * right
            elif tok == "(" or tok in _CONSTANTS or tok in _FUNCTIONS:  # 2pi, 3(4+5)
                right, _ = self.unary()
                value = value * right
            else:
                return value, percent
            self.operated = True
            percent = False

    def unary(self) -> tuple[Number, bool]:
        if self.peek() in ("-", "+"):
            negative = self.take() == "-"
            value, percent = self.unary()
            return (-value if negative else value), percent
        return self.power()

    def power(self) -> tuple[Number, bool]:
        base, percent = self.postfix()
        if self.peek() != "^":
            return base, percent
        self.take()
        self.operated = True
        exponent, _ = self.unary()  # right-associative: 2^3^2 = 2^9
        return _power(base, exponent), False

    def postfix(self) -> tuple[Number, bool]:
        value = self.atom()
        percent = False
        while True:
            tok = self.peek()
            if tok == "!":
                self.take()
                value, percent = _factorial(value), False
            elif tok == "%":
                self.take()
                value, percent = value / 100, True
                if self.peek() == "of":  # 20% of 80
                    self.take()
                    right, _ = self.unary()
                    value, percent = value * right, False
            elif tok in ("°", "deg"):
                self.take()
                value, percent = float(value) * math.pi / 180, False
                continue  # sin 30° alone isn't a sum yet; the function makes it one
            else:
                return value, percent
            self.operated = True

    def atom(self) -> Number:
        tok = self.take()
        if tok == "(":
            self.operated = True
            return self.bracketed()
        if tok in _FUNCTIONS:
            self.operated = True
            if self.peek() == "(":
                self.take()
                arg = self.bracketed()
            else:
                arg, _ = self.unary()  # sqrt 2, sin 30°
            return _FUNCTIONS[tok](arg)
        if tok in _CONSTANTS:
            return _CONSTANTS[tok]
        if tok.startswith(("0x", "0b", "0o")):
            return Fraction(int(tok, 0))
        if _is_number(tok):
            if _is_number(self.peek()):
                raise ValueError("two numbers in a row")  # "1 2" is a search
            if "e" in tok and abs(int(tok.split("e")[1])) > MAX_LITERAL_EXPONENT:
                raise ValueError("number too large")  # Fraction("1e999999999") would hang
            return Fraction(tok)
        raise ValueError(f"unexpected {tok!r}")

    def bracketed(self) -> Number:
        """The inside of ( ... ); a ) still missing at the end is fine while typing."""
        value, _ = self.expr()
        if self.peek() == ")":
            self.take()
        elif self.peek() is not None:
            raise ValueError("missing )")
        return value


def _power(base: Number, exponent: Number) -> Number:
    if isinstance(base, Fraction) and isinstance(exponent, Fraction) and exponent.denominator == 1:
        n = abs(exponent.numerator)
        size = max(base.numerator.bit_length(), base.denominator.bit_length())
        if size * n <= MAX_EXACT_BITS:
            return base**exponent.numerator
    if base < 0 and isinstance(exponent, Fraction) and exponent.denominator % 2 == 1:
        # (-8)^(1/3) = -2: an odd root of a negative number is real.
        magnitude = (-float(base)) ** float(exponent)
        return -magnitude if exponent.numerator % 2 else magnitude
    if base < 0 and float(exponent) != int(float(exponent)):
        raise ValueError("no real root")
    if base == 0 and exponent < 0:
        raise ZeroDivisionError
    return float(base) ** float(exponent)


def _factorial(value: Number) -> Fraction:
    if value != int(value) or value < 0:
        raise ValueError("factorial needs a whole number >= 0")
    if value > MAX_FACTORIAL:
        raise ValueError("factorial too large")
    return Fraction(math.factorial(int(value)))


# --- formatting -----------------------------------------------------------------------


def format_value(value: Number, grouping: bool = True) -> str:
    """12345.5 -> "12,345.5" (or "12345.5" for copying); 1e25 -> "1e25"; 2^64 in full."""
    if isinstance(value, Fraction) and value.denominator == 1:
        n = value.numerator
        if abs(n) < 10**21:
            return f"{n:,}" if grouping else str(n)
        d = Decimal(n)
    elif isinstance(value, Fraction):
        d = Decimal(value.numerator) / Decimal(value.denominator)
    else:
        d = Decimal(value)
    if d == 0:
        return "0"
    d = Decimal(f"{d:.{SIGNIFICANT_DIGITS}g}")
    if PLAIN_RANGE[0] <= abs(d) < PLAIN_RANGE[1]:
        text = f"{d:,f}" if grouping else f"{d:f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text
    mantissa, exponent = f"{d:e}".split("e")
    if "." in mantissa:
        mantissa = mantissa.rstrip("0").rstrip(".")
    return f"{mantissa}e{int(exponent)}"


def as_fraction(value: Number) -> str | None:
    """ "1/3" for a value that is a simple fraction with no short decimal form."""
    if not isinstance(value, Fraction) or value.denominator == 1 or value.denominator > 1000:
        return None
    den = value.denominator
    while den % 2 == 0:
        den //= 2
    while den % 5 == 0:
        den //= 5
    if den == 1:  # 1/4 = 0.25 says it all
        return None
    return f"{value.numerator}/{value.denominator}"
