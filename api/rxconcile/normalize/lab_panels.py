"""Lab panel decomposition.

.. warning::

   **Illustrative proof-of-concept data, not a validated laboratory reference.**
   Panel compositions vary between laboratories -- one lab's "LFT" is not
   another's -- and these were hand-compiled for this build without checking
   against any accredited source. Do not use them for clinical or billing
   decisions. A production system needs a maintained test master, ideally the
   one the reporting laboratory itself uses.

Why this exists: a prescription ordering ``LFT`` and a bill listing SGPT, SGOT,
Bilirubin and Alkaline Phosphatase separately describe the same work. Without
decomposition that reads as one unperformed test plus four unordered ones --
five findings where there is no discrepancy at all.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz import fuzz, process

#: Never resolve a name below this score. A weak match here invents an ordering.
MIN_SCORE: Final[float] = 86.0

#: Canonical panels and the components a bill typically itemises them into.
PANELS: Final[dict[str, tuple[str, ...]]] = {
    "Liver Function Test": (
        "Bilirubin Total",
        "Bilirubin Direct",
        "SGOT",
        "SGPT",
        "Alkaline Phosphatase",
        "Total Protein",
        "Albumin",
    ),
    "Kidney Function Test": (
        "Urea",
        "Creatinine",
        "Uric Acid",
        "Sodium",
        "Potassium",
        "Chloride",
    ),
    "Complete Blood Count": (
        "Haemoglobin",
        "Total WBC Count",
        "RBC Count",
        "Platelet Count",
        "Packed Cell Volume",
        "Differential Count",
    ),
    "Lipid Profile": (
        "Total Cholesterol",
        "Triglycerides",
        "HDL Cholesterol",
        "LDL Cholesterol",
        "VLDL Cholesterol",
    ),
    "Thyroid Profile": ("T3", "T4", "TSH"),
    "Urine Routine and Microscopy": (
        "Urine Colour",
        "Urine pH",
        "Urine Protein",
        "Urine Sugar",
        "Urine Pus Cells",
        "Urine Epithelial Cells",
    ),
    # Ordered as a panel name in practice, but reported as a single analyte.
    "HbA1c": ("HbA1c",),
}

#: How a panel is written on a real prescription.
PANEL_ALIASES: Final[dict[str, str]] = {
    "lft": "Liver Function Test",
    "liver function test": "Liver Function Test",
    "liver function tests": "Liver Function Test",
    "liver profile": "Liver Function Test",
    "kft": "Kidney Function Test",
    "rft": "Kidney Function Test",
    "renal function test": "Kidney Function Test",
    "kidney function test": "Kidney Function Test",
    "cbc": "Complete Blood Count",
    "complete blood count": "Complete Blood Count",
    "haemogram": "Complete Blood Count",
    "hemogram": "Complete Blood Count",
    "cbc with esr": "Complete Blood Count",
    "lipid profile": "Lipid Profile",
    "lipid panel": "Lipid Profile",
    "fasting lipid profile": "Lipid Profile",
    "thyroid profile": "Thyroid Profile",
    "tft": "Thyroid Profile",
    "thyroid function test": "Thyroid Profile",
    "urine r/m": "Urine Routine and Microscopy",
    "urine rm": "Urine Routine and Microscopy",
    "urine routine": "Urine Routine and Microscopy",
    "urine routine and microscopy": "Urine Routine and Microscopy",
    "urine r/e": "Urine Routine and Microscopy",
    "hba1c": "HbA1c",
    "glycosylated haemoglobin": "HbA1c",
    "glycated hemoglobin": "HbA1c",
}

#: How an individual analyte is written on a lab bill.
TEST_ALIASES: Final[dict[str, str]] = {
    "sgpt": "SGPT",
    "alt": "SGPT",
    "sgpt (alt)": "SGPT",
    "alanine aminotransferase": "SGPT",
    "sgot": "SGOT",
    "ast": "SGOT",
    "sgot (ast)": "SGOT",
    "aspartate aminotransferase": "SGOT",
    "alkaline phosphatase": "Alkaline Phosphatase",
    "alp": "Alkaline Phosphatase",
    "bilirubin total": "Bilirubin Total",
    "total bilirubin": "Bilirubin Total",
    "bilirubin (total)": "Bilirubin Total",
    "bilirubin direct": "Bilirubin Direct",
    "direct bilirubin": "Bilirubin Direct",
    "total protein": "Total Protein",
    "albumin": "Albumin",
    "urea": "Urea",
    "blood urea": "Urea",
    "bun": "Urea",
    "creatinine": "Creatinine",
    "serum creatinine": "Creatinine",
    "uric acid": "Uric Acid",
    "sodium": "Sodium",
    "na": "Sodium",
    "potassium": "Potassium",
    "k": "Potassium",
    "chloride": "Chloride",
    "haemoglobin": "Haemoglobin",
    "hemoglobin": "Haemoglobin",
    "hb": "Haemoglobin",
    "total wbc count": "Total WBC Count",
    "wbc count": "Total WBC Count",
    "tlc": "Total WBC Count",
    "total leucocyte count": "Total WBC Count",
    "rbc count": "RBC Count",
    "platelet count": "Platelet Count",
    "platelets": "Platelet Count",
    "packed cell volume": "Packed Cell Volume",
    "pcv": "Packed Cell Volume",
    "haematocrit": "Packed Cell Volume",
    "differential count": "Differential Count",
    "dlc": "Differential Count",
    "differential leucocyte count": "Differential Count",
    "total cholesterol": "Total Cholesterol",
    "cholesterol": "Total Cholesterol",
    "triglycerides": "Triglycerides",
    "tg": "Triglycerides",
    "hdl": "HDL Cholesterol",
    "hdl cholesterol": "HDL Cholesterol",
    "ldl": "LDL Cholesterol",
    "ldl cholesterol": "LDL Cholesterol",
    "vldl": "VLDL Cholesterol",
    "vldl cholesterol": "VLDL Cholesterol",
    "t3": "T3",
    "total t3": "T3",
    "t4": "T4",
    "total t4": "T4",
    "tsh": "TSH",
    "hba1c": "HbA1c",
    "urine colour": "Urine Colour",
    "urine color": "Urine Colour",
    "urine ph": "Urine pH",
    "urine protein": "Urine Protein",
    "urine albumin": "Urine Protein",
    "urine sugar": "Urine Sugar",
    "urine pus cells": "Urine Pus Cells",
    "pus cells": "Urine Pus Cells",
    "urine epithelial cells": "Urine Epithelial Cells",
    "epithelial cells": "Urine Epithelial Cells",
}

Kind = Literal["panel", "test", "unresolved"]


class LabMatch(BaseModel):
    """What a written test name resolved to."""

    model_config = ConfigDict(frozen=True)

    kind: Kind = "unresolved"
    name: str | None = Field(default=None, description="Canonical panel or test name.")
    components: tuple[str, ...] = Field(
        default=(),
        description="For a panel, its component tests. For a single test, just itself. "
        "**Empty when unresolved** -- and an empty expansion must never be treated "
        "as 'this panel contains nothing'.",
    )
    score: float = Field(default=0.0, ge=0.0, le=100.0)
    method: str = Field(default="unresolved")

    @property
    def resolved(self) -> bool:
        return self.kind != "unresolved"


UNRESOLVED: Final[LabMatch] = LabMatch()


def normalise(raw: str) -> str:
    """Fold a written test name into a lookup key."""
    text = raw.strip().lower()
    for token in ("test for ", "test:", "investigation:", "inv:", "adv:"):
        text = text.replace(token, " ")
    for char in "()[]{}.,;:*#":
        text = text.replace(char, " ")
    text = text.replace("&", " and ")
    return " ".join(text.split())


@lru_cache(maxsize=1)
def _panel_keys() -> tuple[str, ...]:
    return tuple(PANEL_ALIASES.keys())


@lru_cache(maxsize=1)
def _test_keys() -> tuple[str, ...]:
    return tuple(TEST_ALIASES.keys())


def resolve(raw: str | None) -> LabMatch:
    """Resolve a written name to a panel or a single test.

    Exact alias first, then fuzzy above :data:`MIN_SCORE`. Anything weaker is
    left unresolved rather than forced: a mis-resolved panel silently changes
    which tests are considered ordered.
    """
    if raw is None:
        return UNRESOLVED
    key = normalise(raw)
    if not key:
        return UNRESOLVED

    panel = PANEL_ALIASES.get(key)
    if panel is not None:
        return LabMatch(kind="panel", name=panel, components=PANELS[panel], score=100.0,
                        method="exact")

    test = TEST_ALIASES.get(key)
    if test is not None:
        return LabMatch(kind="test", name=test, components=(test,), score=100.0,
                        method="exact")

    for keys, kind in ((_panel_keys(), "panel"), (_test_keys(), "test")):
        match = process.extractOne(key, keys, scorer=fuzz.token_sort_ratio,
                                   score_cutoff=MIN_SCORE)
        if match is None:
            continue
        alias, score, _ = match
        if kind == "panel":
            name = PANEL_ALIASES[alias]
            return LabMatch(kind="panel", name=name, components=PANELS[name],
                            score=float(score), method="fuzzy")
        name = TEST_ALIASES[alias]
        return LabMatch(kind="test", name=name, components=(name,), score=float(score),
                        method="fuzzy")

    return UNRESOLVED


def panel_names() -> tuple[str, ...]:
    return tuple(PANELS.keys())
