"""Reference list of commonly prescribed Indian medicines.

.. warning::

   **This is illustrative proof-of-concept data, not a validated drug
   database.** The entries were hand-compiled to exercise brand-to-salt
   resolution on realistic Indian prescriptions. They have not been verified
   against any regulatory source, the strengths listed are indicative rather
   than exhaustive, and the schedule classifications are approximate.

   Do not use this file to make any clinical or dispensing decision. A
   production system must source this from a maintained, licensed drug master.

The CSV has one row per brand, with columns:

``brand_name``
    Marketed brand, e.g. ``Dolo``.
``salt_composition``
    Active ingredients, ``+``-separated for combinations.
``common_strengths``
    ``|``-separated indicative strengths. May be empty.
``form``
    tablet, capsule, syrup, injection, inhaler, cream, ...
``therapeutic_class``
    Coarse grouping, e.g. ``ppi``, ``antibiotic_macrolide``.
``schedule``
    ``OTC``, ``H``, ``H1`` or ``X``. Schedule H and H1 are prescription-only
    under the Drugs and Cosmetics Rules; H1 additionally requires the pharmacy
    to retain a record. This powers a later dispensing rule.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

DATA_FILE: Final[Path] = Path(__file__).parent / "data" / "indian_drugs.csv"

Schedule = Literal["OTC", "H", "H1", "X"]

#: Schedules that may be dispensed only against a valid prescription.
PRESCRIPTION_ONLY_SCHEDULES: Final[frozenset[str]] = frozenset({"H", "H1", "X"})


def normalize_name(value: str) -> str:
    """Fold a brand or salt string into a comparison key.

    Uppercases, replaces punctuation used inconsistently between prescriptions
    and bills (``-``, ``.``, ``/``, ``'``) with spaces, and collapses runs of
    whitespace. ``"Pan-D"``, ``"PAN D"`` and ``"pan  d."`` all fold to
    ``"PAN D"``.
    """
    folded = value.upper()
    for char in "-_.,/'\"()[]":
        folded = folded.replace(char, " ")
    return " ".join(folded.split())


class DrugEntry(BaseModel):
    """One row of the reference list."""

    model_config = ConfigDict(frozen=True)

    brand_name: str = Field(min_length=1)
    salt_composition: str = Field(min_length=1)
    common_strengths: tuple[str, ...] = Field(default=())
    form: str = Field(default="")
    therapeutic_class: str = Field(default="")
    schedule: Schedule = Field(default="H")

    @property
    def key(self) -> str:
        """Comparison key for the brand name."""
        return normalize_name(self.brand_name)

    @property
    def salt_key(self) -> str:
        """Comparison key for the full salt composition."""
        return normalize_name(self.salt_composition)

    @property
    def salts(self) -> tuple[str, ...]:
        """Individual active ingredients, split on ``+``."""
        return tuple(part.strip() for part in self.salt_composition.split("+") if part.strip())

    @property
    def is_combination(self) -> bool:
        return len(self.salts) > 1

    @property
    def requires_prescription(self) -> bool:
        """True for Schedule H, H1 and X entries."""
        return self.schedule in PRESCRIPTION_ONLY_SCHEDULES

    @property
    def is_schedule_h1(self) -> bool:
        """True for Schedule H1, which carries the stricter record-keeping duty."""
        return self.schedule == "H1"


def _parse_row(row: dict[str, str]) -> DrugEntry:
    strengths = tuple(
        part.strip() for part in (row.get("common_strengths") or "").split("|") if part.strip()
    )
    schedule = (row.get("schedule") or "H").strip().upper()
    if schedule not in {"OTC", "H", "H1", "X"}:
        schedule = "H"
    return DrugEntry(
        brand_name=(row["brand_name"] or "").strip(),
        salt_composition=(row["salt_composition"] or "").strip(),
        common_strengths=strengths,
        form=(row.get("form") or "").strip(),
        therapeutic_class=(row.get("therapeutic_class") or "").strip(),
        schedule=schedule,  # type: ignore[arg-type]
    )


@lru_cache(maxsize=1)
def load_entries() -> tuple[DrugEntry, ...]:
    """Load and cache every dictionary row."""
    with DATA_FILE.open(encoding="utf-8", newline="") as handle:
        return tuple(_parse_row(row) for row in csv.DictReader(handle))


@lru_cache(maxsize=1)
def brand_index() -> dict[str, DrugEntry]:
    """Normalised brand key -> entry. First occurrence wins."""
    index: dict[str, DrugEntry] = {}
    for entry in load_entries():
        index.setdefault(entry.key, entry)
    return index


@lru_cache(maxsize=1)
def salt_index() -> dict[str, tuple[DrugEntry, ...]]:
    """Normalised salt-composition key -> every entry sharing that composition."""
    index: dict[str, list[DrugEntry]] = {}
    for entry in load_entries():
        index.setdefault(entry.salt_key, []).append(entry)
    return {key: tuple(value) for key, value in index.items()}


@lru_cache(maxsize=1)
def single_salt_index() -> dict[str, str]:
    """Normalised single-ingredient key -> its canonical display name.

    Lets ``"PARACETAMOL 650MG"`` resolve to the ingredient even though
    paracetamol is not itself a brand in the list.
    """
    index: dict[str, str] = {}
    for entry in load_entries():
        for salt in entry.salts:
            index.setdefault(normalize_name(salt), salt)
    return index


def find_brand(name: str) -> DrugEntry | None:
    """Exact (normalised) brand lookup."""
    return brand_index().get(normalize_name(name))


def entries_for_salt(salt_composition: str) -> tuple[DrugEntry, ...]:
    """Every brand sharing ``salt_composition``."""
    return salt_index().get(normalize_name(salt_composition), ())


def scheduled_entries(schedule: str) -> Iterable[DrugEntry]:
    """Entries in a given schedule."""
    wanted = schedule.strip().upper()
    return (entry for entry in load_entries() if entry.schedule == wanted)
