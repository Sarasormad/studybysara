"""Auto-grading for short answers and multiple choice.

Numbers are compared by exact value, so "3/4", "0.75", ".75" and " 0.750 " all match.
Word answers ignore case and extra spaces.
"""
import re
from fractions import Fraction

_MIXED = re.compile(r"^([+-]?)(\d+)\s+(\d+)\s*/\s*(\d+)$")       # 1 1/2
_FRACTION = re.compile(r"^([+-]?\d+)\s*/\s*([+-]?\d+)$")          # 3/4, -3/4
_DECIMAL = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")                # 12, 0.75, .75, 3.


def parse_number(text):
    """Return the exact value of a typed number as a Fraction, or None if it isn't one."""
    s = (text or "").strip()
    s = s.replace("−", "-").replace(",", "")    # unicode minus sign, thousands separators
    if s.endswith("%"):
        s = s[:-1].strip()
    if m := _MIXED.match(s):
        sign, whole, num, den = m.groups()
        if int(den) == 0:
            return None
        value = int(whole) + Fraction(int(num), int(den))
        return -value if sign == "-" else value
    if m := _FRACTION.match(s):
        num, den = int(m.group(1)), int(m.group(2))
        return None if den == 0 else Fraction(num, den)
    if _DECIMAL.match(s.replace(" ", "")):
        return Fraction(s.replace(" ", ""))
    return None


def normalize_words(text):
    return " ".join((text or "").lower().split())


def answers_match(submitted, correct):
    """True if the student's answer is equivalent to the answer key."""
    expected = parse_number(correct)
    if expected is not None:
        return parse_number(submitted) == expected
    return normalize_words(submitted) == normalize_words(correct)


def format_number(value):
    """Canonical text for a Fraction: '3', '-3/4'."""
    value = Fraction(value)
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
