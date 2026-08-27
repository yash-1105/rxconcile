"""Resolve a raw drug string to a canonical dictionary entry.

Three stages, tried in order, stopping at the first that succeeds:

1. **exact** -- normalised brand match.
2. **fuzzy** -- ``rapidfuzz.token_set_ratio`` against brand names.
3. **salt_equivalent** -- the string names an active ingredient rather than a
   brand, e.g. ``"PARACETAMOL 650MG"``.

Otherwise **unresolved**. Nothing below :data:`MIN_SCORE` is ever resolved.

.. note::

   ``token_set_ratio`` scores a subset against its superset at **100**:
   ``"PAN"`` versus ``"PAN D"`` is a perfect match by that metric, yet
   pantoprazole and pantoprazole+domperidone are different drugs. Exact
   matching therefore runs first, and the fuzzy stage refuses to choose when
   the top score is tied between candidates with different salt compositions --
   an ambiguous match returns ``unresolved`` rather than picking arbitrarily.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz import fuzz, process

from rxconcile.normalize.drug_dictionary import (
    DrugEntry,
    brand_index,
    find_brand,
    load_entries,
    normalize_name,
    single_salt_index,
)

MatchMethod = Literal["exact", "fuzzy", "salt_equivalent", "unresolved"]

#: Never resolve below this score. A weak match is worse than no match: it
#: silently substitutes one drug for another.
MIN_SCORE: Final[float] = 80.0

#: Dosage-form prefixes to strip before matching. Prescriptions and bills both
#: prefix the drug with its form, and the form is captured separately.
_FORM_PREFIXES: Final[frozenset[str]] = frozenset(
    {"TAB", "TABS", "TABLET", "TABLETS", "T", "CAP", "CAPS", "CAPSULE", "CAPSULES",
     "C", "SYP", "SYR", "SYRUP", "SUSP", "SUSPENSION", "INJ", "INJECTION",
     "OINT", "OINTMENT", "CREAM", "GEL", "DROP", "DROPS", "LOTION", "POWDER",
     "SACHET", "SOLUTION", "SPRAY", "INHALER", "ROTACAP", "RESPULE"}
)

#: Sig tokens that may trail the drug name on a prescription line.
_SIG_TOKENS: Final[frozenset[str]] = frozenset(
    {"OD", "BD", "BID", "TDS", "TID", "QID", "QDS", "HS", "SOS", "PRN", "STAT",
     "AC", "PC", "NOCTE", "OM", "MANE", "PO", "IV", "IM", "SC", "ORAL"}
)

_STRENGTH_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d+(?:\.\d+)?(?:MG|MGS|G|GM|GMS|MCG|UG|ML|L|IU|%|)$"
)
_DURATION_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^(?:X)?\d+(?:/\d+)?[A-Z]*$")


class CanonicalDrug(BaseModel):
    """The outcome of resolving one raw drug string."""

    model_config = ConfigDict(frozen=True)

    name: str | None = Field(
        default=None,
        description="Canonical display name: the brand for a brand match, the "
        "ingredient for a salt match, None when unresolved.",
    )
    salt: str | None = Field(
        default=None, description="Salt composition, None when unresolved."
    )
    source: str | None = Field(
        default=None,
        description="Dictionary brand whose row produced the match; None when "
        "unresolved or when only an ingredient was matched.",
    )
    match_score: float = Field(
        default=0.0, ge=0.0, le=100.0, description="0-100. Zero when unresolved."
    )
    method: MatchMethod = Field(default="unresolved")

    @property
    def resolved(self) -> bool:
        return self.method != "unresolved"


UNRESOLVED: Final[CanonicalDrug] = CanonicalDrug()


def clean_drug_string(raw: str) -> str:
    """Strip dosage form, strength and sig noise, leaving the drug name.

    ``"TAB. DOLO 650"`` -> ``"DOLO"``;
    ``"Cap Augmentin 625 BD x 5/7"`` -> ``"AUGMENTIN"``;
    ``"T. Pan 40 OD x 10d"`` -> ``"PAN"``.
    """
    text = normalize_name(raw.replace("×", " x ").replace("×", " x "))
    tokens = text.split()

    while tokens and tokens[0] in _FORM_PREFIXES:
        tokens.pop(0)

    kept: list[str] = []
    for token in tokens:
        if token in _SIG_TOKENS or token in _FORM_PREFIXES:
            continue
        if token == "X":
            continue
        if _STRENGTH_TOKEN_RE.match(token):
            continue
        if _DURATION_TOKEN_RE.match(token) and any(ch.isdigit() for ch in token):
            continue
        kept.append(token)

    return " ".join(kept).strip()


@lru_cache(maxsize=1)
def _brand_keys() -> tuple[str, ...]:
    return tuple(brand_index().keys())


@lru_cache(maxsize=1)
def _salt_keys() -> tuple[str, ...]:
    return tuple(single_salt_index().keys())


def _entry_for_key(key: str) -> DrugEntry | None:
    return brand_index().get(key)


def _pair_score(query: str, candidate: str) -> float:
    """Conservative similarity: the weaker of token_set and token_sort.

    ``token_set_ratio`` alone scores a subset against its superset at 100, so
    ``"PAN DX"`` matches ``"PAN"`` perfectly while almost certainly meaning
    ``"Pan-D"`` -- a wrong-drug substitution. ``token_sort_ratio`` penalises the
    unmatched remainder (67 for that pair), so taking the minimum keeps genuine
    typos (``"AUGMENTN"`` -> ``"AUGMENTIN"``, 94/94) while rejecting a partial
    match that discards meaningful text.
    """
    return min(
        float(fuzz.token_set_ratio(query, candidate)),
        float(fuzz.token_sort_ratio(query, candidate)),
    )


def _fuzzy_brand(query: str) -> CanonicalDrug | None:
    """Best fuzzy brand match, or None when weak or ambiguous."""
    prefiltered = process.extract(
        query, _brand_keys(), scorer=fuzz.token_set_ratio, limit=25, score_cutoff=MIN_SCORE
    )
    if not prefiltered:
        return None

    rescored = [
        (key, _pair_score(query, key))
        for key, _score, _index in prefiltered
    ]
    rescored = [(key, score) for key, score in rescored if score >= MIN_SCORE]
    if not rescored:
        return None
    rescored.sort(key=lambda pair: pair[1], reverse=True)

    best_score = rescored[0][1]
    tied = [key for key, score in rescored if score >= best_score - 1e-9]

    # token_set_ratio scores subsets at 100, so a tie can hide two genuinely
    # different drugs (Pan vs Pan-D). Refuse rather than choose.
    salts = {
        entry.salt_key
        for entry in (_entry_for_key(key) for key in tied)
        if entry is not None
    }
    if len(salts) > 1:
        return None

    entry = _entry_for_key(tied[0])
    if entry is None:
        return None
    return CanonicalDrug(
        name=entry.brand_name,
        salt=entry.salt_composition,
        source=entry.brand_name,
        match_score=float(best_score),
        method="fuzzy",
    )


def _salt_match(query: str) -> CanonicalDrug | None:
    """Resolve a string that names an active ingredient rather than a brand."""
    index = single_salt_index()
    if query in index:
        return CanonicalDrug(
            name=index[query], salt=index[query], match_score=100.0, method="salt_equivalent"
        )
    match = process.extractOne(
        query, _salt_keys(), scorer=fuzz.token_set_ratio, score_cutoff=MIN_SCORE
    )
    if match is None:
        return None
    key, score, _ = match
    return CanonicalDrug(
        name=index[key], salt=index[key], match_score=float(score), method="salt_equivalent"
    )


def resolve(raw: str | None) -> CanonicalDrug:
    """Resolve ``raw`` to a canonical drug, or return :data:`UNRESOLVED`.

    Never resolves below :data:`MIN_SCORE`, and never picks between tied
    candidates whose salts differ.
    """
    if raw is None:
        return UNRESOLVED
    query = clean_drug_string(raw)
    if not query:
        return UNRESOLVED

    entry = find_brand(query)
    if entry is not None:
        return CanonicalDrug(
            name=entry.brand_name,
            salt=entry.salt_composition,
            source=entry.brand_name,
            match_score=100.0,
            method="exact",
        )

    # An ingredient name is not a brand; check it before fuzzing brand names,
    # so "PARACETAMOL" resolves as an ingredient rather than fuzzing to
    # "Paracip" or another brand that merely looks similar.
    exact_salt = single_salt_index().get(query)
    if exact_salt is not None:
        return CanonicalDrug(
            name=exact_salt, salt=exact_salt, match_score=100.0, method="salt_equivalent"
        )

    fuzzy = _fuzzy_brand(query)
    if fuzzy is not None:
        return fuzzy

    salt = _salt_match(query)
    if salt is not None:
        return salt

    return UNRESOLVED


def entry_for(drug: CanonicalDrug) -> DrugEntry | None:
    """The dictionary row behind a resolved brand match, if any."""
    if drug.source is None:
        return None
    return find_brand(drug.source)


def all_brands() -> tuple[str, ...]:
    """Every brand name in the dictionary."""
    return tuple(entry.brand_name for entry in load_entries())
