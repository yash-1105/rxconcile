"""Sig interpretation: frequency, duration and expected quantity.

This module owns the conversions the extractor is forbidden to make. Every
assumption here is an explicit, named, tested constant rather than an invisible
step inside an LLM call -- see :data:`DAYS_PER_MONTH`.

Every function returns ``None`` rather than a guess when an input is missing or
unrecognised. A null propagates harmlessly; a fabricated number becomes a
discrepancy finding against a figure nobody wrote down.
"""

from __future__ import annotations

import re
from typing import Final

# --------------------------------------------------------------------------
# Explicit assumptions
# --------------------------------------------------------------------------

#: Days in a prescribed "month".
#:
#: A prescription that says "4 months" does not state how long a month is, so
#: converting it requires an assumption. Thirty days is the common dispensing
#: convention and is used here **explicitly**, so that the assumption is
#: visible, testable, and changeable in one place. It is deliberately NOT made
#: inside the extraction prompt, where it would be invisible and unverifiable.
DAYS_PER_MONTH: Final[int] = 30

#: Days in a prescribed week. Not an assumption; a definition.
DAYS_PER_WEEK: Final[int] = 7

# --------------------------------------------------------------------------
# Frequency
# --------------------------------------------------------------------------

#: Latin and Indian dosing abbreviations -> administrations per day.
#: None means "as needed", where a total quantity cannot be derived at all.
_FREQUENCY_CODES: Final[dict[str, float | None]] = {
    "OD": 1.0, "ONCE DAILY": 1.0, "QD": 1.0, "QDAY": 1.0,
    "BD": 2.0, "BID": 2.0, "TWICE DAILY": 2.0,
    "TDS": 3.0, "TID": 3.0, "THRICE DAILY": 3.0,
    "QID": 4.0, "QDS": 4.0,
    "HS": 1.0, "NOCTE": 1.0, "AT BEDTIME": 1.0,
    "OM": 1.0, "MANE": 1.0, "ON": 1.0,
    "STAT": 1.0,
    "SOS": None, "PRN": None, "AS NEEDED": None, "IF REQUIRED": None,
}

#: Abbreviations meaning "as needed", for which quantity is uncheckable.
_AS_NEEDED: Final[frozenset[str]] = frozenset({"SOS", "PRN", "AS NEEDED", "IF REQUIRED"})

#: Modifiers that describe timing, not frequency, and are ignored when present
#: alongside a real frequency.
_TIMING_ONLY: Final[frozenset[str]] = frozenset({"AC", "PC", "BEFORE FOOD", "AFTER FOOD"})

_VULGAR_FRACTIONS: Final[dict[str, float]] = {
    "½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3,
}

_POSITIONAL_SEPARATORS: Final[str] = "-+"

#: Dash variants that appear in transcribed sig notation. Prescriptions are
#: written with long dashes and the extractor copies them verbatim, as it must,
#: so "1 - 0 - 1" arrives as "1 \u2014 0 \u2014 1". Folding them here keeps the
#: verbatim rule intact while still letting the parser read the schedule.
_DASH_VARIANTS: Final[str] = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d"


def _expand_fractions(token: str) -> str:
    for glyph, value in _VULGAR_FRACTIONS.items():
        if glyph in token:
            token = token.replace(glyph, f"+{value}" if token.index(glyph) > 0 else f"{value}")
    return token


def _token_to_dose(token: str) -> float | None:
    """Interpret one positional slot, e.g. ``1``, ``0``, ``1/2``, ``2½``."""
    text = token.strip()
    if not text:
        return None
    for glyph, value in _VULGAR_FRACTIONS.items():
        if text.endswith(glyph):
            head = text[: -len(glyph)].strip()
            base = float(head) if head.isdigit() else 0.0
            return base + value
        if text == glyph:
            return value
    if re.fullmatch(r"\d+/\d+", text):
        numerator, denominator = text.split("/")
        return float(numerator) / float(denominator)
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    return None


def normalize_frequency(raw: str | None) -> str | None:
    """Uppercase and collapse a frequency string, or None if blank."""
    if raw is None:
        return None
    folded = raw
    for dash in _DASH_VARIANTS:
        folded = folded.replace(dash, "-")
    text = " ".join(folded.upper().split())
    return text or None


def doses_per_day(raw: str | None) -> float | None:
    """Administrations per day implied by a frequency string.

    ``"1-0-1"`` -> 2, ``"1-1-1"`` -> 3, ``"BD"`` -> 2, ``"TDS"`` -> 3,
    ``"OD"`` -> 1, ``"HS"`` -> 1, ``"QID"`` -> 4.

    ``"SOS"`` and ``"PRN"`` return None: an as-needed drug has no derivable
    total, and returning a number would invent one. If an as-needed marker
    appears anywhere in the string it wins, even alongside a positional
    schedule -- ``"0-0-1 HS SOS"`` is uncheckable.
    """
    text = normalize_frequency(raw)
    if text is None:
        return None

    words = set(re.split(r"[\s,]+", text))
    if words & _AS_NEEDED or any(marker in text for marker in _AS_NEEDED):
        return None

    if text in _FREQUENCY_CODES:
        return _FREQUENCY_CODES[text]

    # Positional schedule: "1-0-1", "1+0+1", "0+0+2½", "1/2-0-1/2".
    for separator in _POSITIONAL_SEPARATORS:
        if separator in text:
            slots = [slot.strip() for slot in text.split(separator)]
            doses = [_token_to_dose(slot) for slot in slots]
            if len(doses) >= 2 and all(dose is not None for dose in doses):
                total = sum(dose for dose in doses if dose is not None)
                return total if total > 0 else 0.0

    # A bare code embedded in surrounding text, e.g. "BD x 5 days".
    for token in re.split(r"[\s,]+", text):
        if token in _FREQUENCY_CODES and token not in _TIMING_ONLY:
            return _FREQUENCY_CODES[token]

    return None


# --------------------------------------------------------------------------
# Duration
# --------------------------------------------------------------------------

_DURATION_UNIT_DAYS: Final[dict[str, int]] = {
    "DAY": 1, "DAYS": 1, "D": 1, "DY": 1,
    "WEEK": DAYS_PER_WEEK, "WEEKS": DAYS_PER_WEEK, "WK": DAYS_PER_WEEK,
    "WKS": DAYS_PER_WEEK, "W": DAYS_PER_WEEK,
    "MONTH": DAYS_PER_MONTH, "MONTHS": DAYS_PER_MONTH,
    "MON": DAYS_PER_MONTH, "MO": DAYS_PER_MONTH, "MTH": DAYS_PER_MONTH,
}

#: Denominator convention in "n/7" style notation: 7 = days of a week,
#: 52 = weeks of a year, 12 = months of a year.
_FRACTION_DENOMINATOR_DAYS: Final[dict[int, int]] = {
    7: 1,
    52: DAYS_PER_WEEK,
    12: DAYS_PER_MONTH,
}

# --------------------------------------------------------------------------
# Minimal Bengali duration support
# --------------------------------------------------------------------------
#
# The system targets English/Latin-script documents (see docs/DESIGN_DECISIONS.md
# section 4). This is a deliberate, narrow exception: two of the four real sample
# prescriptions write their course length in Bengali, and without these few
# mappings they yield no expected_quantity at all -- which would ship the
# quantity rules untested against real input.
#
# Scope is strictly Bengali digits and the three duration unit words. Do not
# extend non-Latin handling beyond this module.

#: Bengali-Assamese digits U+09E6..U+09EF -> ASCII.
_BENGALI_DIGITS: Final[dict[str, str]] = {
    "০": "0", "১": "1", "২": "2", "৩": "3", "৪": "4",
    "৫": "5", "৬": "6", "৭": "7", "৮": "8", "৯": "9",
}

#: Bengali duration unit words -> the Latin token the parser already understands.
_BENGALI_DURATION_UNITS: Final[dict[str, str]] = {
    "দিন": "DAYS",
    "সপ্তাহ": "WEEKS",
    "মাস": "MONTHS",
}


def transliterate_bengali_duration(text: str) -> str:
    """Rewrite Bengali digits and duration words into their Latin equivalents.

    ``"৪ মাস"`` -> ``"4 MONTHS"``, ``"৭ সপ্তাহ"`` -> ``"7 WEEKS"``,
    ``"৪৫ দিন"`` -> ``"45 DAYS"``. Text containing neither is returned unchanged.
    """
    converted = "".join(_BENGALI_DIGITS.get(char, char) for char in text)
    for bengali, latin in _BENGALI_DURATION_UNITS.items():
        converted = converted.replace(bengali, f" {latin} ")
    return " ".join(converted.split())


_DURATION_FRACTION_RE: Final[re.Pattern[str]] = re.compile(r"\b(\d+)\s*/\s*(7|52|12)\b")
#: No leading \b: "X5D" fuses the multiplier to the digit, so there is no word
#: boundary before the 5.
_DURATION_UNIT_RE: Final[re.Pattern[str]] = re.compile(
    r"(\d+(?:\.\d+)?)\s*([A-Z]+)\b"
)


def duration_to_days(raw: str | None) -> int | None:
    """Convert a written course length into whole days, or None.

    ``"x 5 days"`` -> 5, ``"x5d"`` -> 5, ``"5/7"`` -> 5 (five days of a
    seven-day week), ``"2 weeks"`` -> 14, ``"4 months"`` -> 120 via
    :data:`DAYS_PER_MONTH`, ``"1/12"`` -> 30, ``"2/52"`` -> 14.

    Bengali digits and the three Bengali duration words are handled as a narrow
    exception (see :func:`transliterate_bengali_duration`): ``"৪ মাস"`` -> 120.

    Returns None for anything not recognised, including open-ended instructions
    such as "continue". **A None here is correct**; a
    fabricated duration propagates into ``expected_quantity`` and surfaces as a
    quantity discrepancy against a number nobody wrote.
    """
    if raw is None:
        return None
    folded = transliterate_bengali_duration(raw)
    for dash in _DASH_VARIANTS:
        folded = folded.replace(dash, "-")
    text = " ".join(folded.upper().split())
    if not text:
        return None

    fraction = _DURATION_FRACTION_RE.search(text)
    if fraction is not None:
        count = int(fraction.group(1))
        multiplier = _FRACTION_DENOMINATOR_DAYS[int(fraction.group(2))]
        return count * multiplier

    match = _DURATION_UNIT_RE.search(text)
    if match is not None:
        amount = float(match.group(1))
        unit = match.group(2)
        if unit in _DURATION_UNIT_DAYS:
            return int(round(amount * _DURATION_UNIT_DAYS[unit]))
        return None

    # A bare number with no unit is ambiguous and is deliberately not assumed
    # to mean days.
    return None


# --------------------------------------------------------------------------
# Expected quantity
# --------------------------------------------------------------------------


def expected_quantity(
    doses_per_day_value: float | None,
    duration_days: int | None,
    dose_per_administration: float | None = 1.0,
) -> float | None:
    """Total units a course should require.

    ``doses_per_day x duration_days x dose_per_administration``.

    Returns None if any input is missing, which includes an as-needed frequency
    and an unparseable duration. Never substitutes a default for a missing
    input: the whole point of this number is to be compared against a billed
    quantity, and a comparison against an invented expectation is worse than no
    comparison.
    """
    if doses_per_day_value is None or duration_days is None:
        return None
    if dose_per_administration is None:
        return None
    if doses_per_day_value <= 0 or duration_days <= 0 or dose_per_administration <= 0:
        return None
    return doses_per_day_value * duration_days * dose_per_administration
