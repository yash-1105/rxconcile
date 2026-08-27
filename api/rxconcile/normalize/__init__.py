"""Deterministic normalisation: drug identity, units, and sig interpretation.

Everything here is plain Python. The extractor transcribes; this layer converts,
and every assumption it makes is a named constant that a test can pin.
"""

from rxconcile.normalize.drug_dictionary import DrugEntry, find_brand, load_entries
from rxconcile.normalize.matcher import CanonicalDrug, MatchMethod, resolve
from rxconcile.normalize.sig import (
    DAYS_PER_MONTH,
    DAYS_PER_WEEK,
    doses_per_day,
    duration_to_days,
    expected_quantity,
    is_positional,
)
from rxconcile.normalize.units import (
    ParsedPack,
    Strength,
    canonical_unit,
    normalize_strength,
    parse_pack_size,
    parse_strength,
)

__all__ = [
    "DAYS_PER_MONTH",
    "DAYS_PER_WEEK",
    "CanonicalDrug",
    "DrugEntry",
    "MatchMethod",
    "ParsedPack",
    "Strength",
    "canonical_unit",
    "doses_per_day",
    "duration_to_days",
    "expected_quantity",
    "is_positional",
    "find_brand",
    "load_entries",
    "normalize_strength",
    "parse_pack_size",
    "parse_strength",
    "resolve",
]
