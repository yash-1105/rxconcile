"""Strength and pack-size canonicalisation.

Two jobs, both deliberately conservative: anything unrecognised returns ``None``
rather than a guess. An unparsed value is handled downstream; a wrongly parsed
one silently corrupts a comparison.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Strength units
# --------------------------------------------------------------------------

#: Spelling variants -> canonical unit. Keys are lowercased and stripped of dots.
_UNIT_ALIASES: Final[dict[str, str]] = {
    "mg": "mg", "mgs": "mg", "milligram": "mg", "milligrams": "mg",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "mcg": "mcg", "ug": "mcg", "µg": "mcg", "μg": "mcg",
    "microgram": "mcg", "micrograms": "mcg",
    "ml": "ml", "millilitre": "ml", "milliliter": "ml", "cc": "ml",
    "l": "l", "litre": "l", "liter": "l",
    "iu": "IU", "units": "IU", "unit": "IU", "u": "IU",
    "%": "%", "percent": "%",
}

#: Multiplier to convert a unit into its canonical base.
#: Grams become milligrams so that 1 g and 1000 mg compare equal.
_TO_BASE: Final[dict[str, tuple[float, str]]] = {
    "g": (1000.0, "mg"),
    "mg": (1.0, "mg"),
    "mcg": (1.0, "mcg"),
    "ml": (1.0, "ml"),
    "l": (1000.0, "ml"),
    "IU": (1.0, "IU"),
    "%": (1.0, "%"),
}

_STRENGTH_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%|[a-zA-Zµμ]+)?"
)


class Strength(BaseModel):
    """A single canonicalised strength component."""

    model_config = ConfigDict(frozen=True)

    value: float = Field(ge=0)
    unit: str | None = Field(
        default=None, description="Canonical unit, or None if none was written."
    )

    def __str__(self) -> str:
        return f"{self.value:g}{self.unit or ''}"


def canonical_unit(raw: str | None) -> str | None:
    """Map a unit spelling to its canonical form, or None if unrecognised.

    ``mg``/``MG``/``mgs`` -> ``mg``; ``mcg``/``ug``/``µg`` -> ``mcg``;
    ``ml``/``mL`` -> ``ml``; ``gm``/``g`` -> ``g`` (converted to ``mg`` by
    :func:`normalize_strength`).
    """
    if raw is None:
        return None
    cleaned = raw.strip().lower().replace(".", "")
    if not cleaned:
        return None
    return _UNIT_ALIASES.get(cleaned)


def normalize_strength(value: float | None, unit: str | None) -> Strength | None:
    """Canonicalise a value/unit pair, converting grams to milligrams.

    Returns None when there is no value. An unrecognised unit yields a Strength
    carrying the value with ``unit=None`` rather than inventing one -- the
    number was legible even if the unit was not.
    """
    if value is None:
        return None
    canonical = canonical_unit(unit)
    if canonical is None:
        return Strength(value=float(value), unit=None)
    factor, base = _TO_BASE[canonical]
    return Strength(value=float(value) * factor, unit=base)


def parse_strength(raw: str | None) -> tuple[Strength, ...]:
    """Parse a written strength, including combinations, into components.

    ``"500mg"`` -> one component. ``"500+125mg"`` -> two components, the unit
    carried across from the last component that states one, which is how
    combination strengths are written on Indian packs. ``"37.5mg+325mg"`` ->
    two explicit components. Returns an empty tuple when nothing parses.
    """
    if raw is None:
        return ()
    text = raw.strip()
    if not text:
        return ()

    chunks = [chunk for chunk in re.split(r"[+/]", text) if chunk.strip()]
    parsed: list[tuple[float, str | None]] = []
    for chunk in chunks:
        match = _STRENGTH_RE.search(chunk)
        if match is None:
            continue
        value = float(match.group("value"))
        parsed.append((value, canonical_unit(match.group("unit"))))
    if not parsed:
        return ()

    # "500+125mg": only the final chunk names the unit; apply it to the rest.
    trailing = next((unit for _, unit in reversed(parsed) if unit is not None), None)
    out: list[Strength] = []
    for value, unit in parsed:
        effective = unit if unit is not None else trailing
        normalized = normalize_strength(value, effective)
        if normalized is not None:
            out.append(normalized)
    return tuple(out)


def strengths_equal(left: Strength | None, right: Strength | None) -> bool:
    """Compare two canonicalised strengths, tolerating float representation."""
    if left is None or right is None:
        return False
    if left.unit != right.unit:
        return False
    return abs(left.value - right.value) < 1e-6


# --------------------------------------------------------------------------
# Pack sizes
# --------------------------------------------------------------------------


class ParsedPack(BaseModel):
    """A pharmacy pack notation, resolved into units or a volume.

    Exactly one of :attr:`units_per_pack` and :attr:`volume_ml` is normally set.
    Both None means the notation was not recognised, which is an acceptable
    outcome -- an unparsed pack is fine, a wrong one is not.
    """

    model_config = ConfigDict(frozen=True)

    units_per_pack: int | None = Field(default=None, gt=0)
    volume_ml: float | None = Field(default=None, gt=0)
    raw: str = Field(description="The notation exactly as printed.")
    method: str = Field(
        description="How it was resolved: apostrophe_s, count_unit, multiplier, "
        "strip_of, volume, singular, or unrecognised."
    )

    @property
    def resolved(self) -> bool:
        return self.units_per_pack is not None or self.volume_ml is not None


_VOLUME_UNITS: Final[frozenset[str]] = frozenset({"ml", "l"})

#: Container words that denote exactly one dispensed item.
_SINGULAR_CONTAINERS: Final[frozenset[str]] = frozenset(
    {"vial", "tube", "bottle", "ampoule", "ampule", "pen", "jar", "sachet", "inhaler", "kit"}
)

#: Words for counted dosage units, e.g. "10 TAB", "15 CAPS".
_COUNT_UNITS: Final[frozenset[str]] = frozenset(
    {"tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule", "capsules",
     "pcs", "pc", "piece", "pieces", "nos", "no", "s"}
)

_PACK_APOSTROPHE_RE: Final[re.Pattern[str]] = re.compile(r"^(\d+)\s*'?\s*S$", re.IGNORECASE)
_PACK_MULTIPLIER_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\d+)\s*[xX×*]\s*(\d+)$"
)
_PACK_STRIP_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:STRIP|PACK|BOX|BLISTER)\s+OF\s+(\d+)$", re.IGNORECASE
)
_PACK_VOLUME_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(ML|L)\b", re.IGNORECASE
)
_PACK_COUNT_RE: Final[re.Pattern[str]] = re.compile(r"^(\d+)\s*([A-Za-z]+)$")
_PACK_SINGULAR_RE: Final[re.Pattern[str]] = re.compile(r"^(\d+)\s*([A-Za-z]+)$")


def parse_pack_size(raw: str | None) -> ParsedPack | None:
    """Resolve an Indian pharmacy pack notation into units or a volume.

    Recognised forms::

        "10'S", "10S", "10 TAB", "1x10", "STRIP OF 10"  -> units_per_pack
        "15ML", "100 ML", "60ml syrup"                  -> volume_ml
        "1'S", "1 VIAL", "1 TUBE"                       -> units_per_pack = 1

    Returns None when ``raw`` is None or blank. Returns a ParsedPack with
    ``method="unrecognised"`` and both values None when the notation is present
    but not understood -- the caller can then decline to compute rather than
    compute wrongly.

    This is load-bearing for quantity reconciliation: a bill line reading ``2``
    against a pack marked ``10'S`` means twenty tablets, not two.
    """
    if raw is None:
        return None
    text = " ".join(raw.strip().split())
    if not text:
        return None

    def pack(**kwargs: object) -> ParsedPack:
        return ParsedPack(raw=raw, **kwargs)  # type: ignore[arg-type]

    # Volume first: "15ML" must not be read as 15 of something.
    volume = _PACK_VOLUME_RE.match(text)
    if volume is not None:
        amount = float(volume.group(1))
        millilitres = amount * (1000.0 if volume.group(2).lower() == "l" else 1.0)
        return pack(volume_ml=millilitres, method="volume")

    strip = _PACK_STRIP_RE.match(text)
    if strip is not None:
        return pack(units_per_pack=int(strip.group(1)), method="strip_of")

    multiplier = _PACK_MULTIPLIER_RE.match(text)
    if multiplier is not None:
        # "1x10" is one strip of ten; "2x15" is thirty units.
        return pack(
            units_per_pack=int(multiplier.group(1)) * int(multiplier.group(2)),
            method="multiplier",
        )

    apostrophe = _PACK_APOSTROPHE_RE.match(text)
    if apostrophe is not None:
        return pack(units_per_pack=int(apostrophe.group(1)), method="apostrophe_s")

    counted = _PACK_COUNT_RE.match(text)
    if counted is not None:
        count, word = int(counted.group(1)), counted.group(2).lower()
        if word in _VOLUME_UNITS:
            return pack(volume_ml=float(count), method="volume")
        if word in _COUNT_UNITS:
            return pack(units_per_pack=count, method="count_unit")
        if word in _SINGULAR_CONTAINERS:
            # "1 VIAL" is one unit; "5 VIAL" is five units.
            return pack(units_per_pack=count, method="singular")

    return pack(method="unrecognised")
