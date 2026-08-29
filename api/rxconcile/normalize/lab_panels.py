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

import re
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

#: A parenthetical component list: "Thyroid Profile (T3, T4, TSH)".
#:
#: Removed WHOLE before lookup. ``normalise`` only strips the bracket
#: characters, which left "thyroid profile t3 t4 tsh" -- a string too long to
#: fuzzy-match the panel it plainly names.
_PARENTHETICAL: Final[re.Pattern[str]] = re.compile(r"\([^)]*\)|\[[^\]]*\]")

#: Panel/component notation: "Lipid Profile — Total Cholesterol".
#:
#: Em-dash, en-dash, figure dash, minus, colon and pipe always split. A plain
#: hyphen splits only when spaced, so a hyphenated name is not torn in half.
_SEPARATOR: Final[re.Pattern[str]] = re.compile(r"\s*[\u2014\u2013\u2012\u2212:|]\s*|\s+-\s+")

#: Analytes a laboratory CALCULATES rather than assays and bills separately.
#:
#: VLDL is derived from triglycerides, so a lipid profile billed as four lines
#: is complete, not partial. Without this a correct bill raises PANEL_PARTIAL
#: for a component no laboratory would ever charge for.
#:
#: Kept in the panel above rather than deleted: a report DOES list VLDL, and a
#: bill that itemises it should still count as covering it. This only stops its
#: absence being called a shortfall.
DERIVED_COMPONENTS: Final[frozenset[str]] = frozenset({"VLDL Cholesterol"})


def required_components(panel: str) -> tuple[str, ...]:
    """Components whose absence from a bill is a genuine shortfall."""
    return tuple(c for c in PANELS.get(panel, ()) if c not in DERIVED_COMPONENTS)


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


def _lookup(key: str) -> LabMatch:
    """Exact alias, then fuzzy above :data:`MIN_SCORE`. Nothing weaker."""
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


def _component_of(panel: LabMatch, component: LabMatch) -> LabMatch | None:
    """The analyte a "Panel — Component" line actually bills.

    Returns the COMPONENT, not the panel: the bill charged for one analyte, and
    reporting the whole panel from one line would satisfy an order six lines
    early.
    """
    if panel.kind != "panel" or component.kind != "test" or component.name is None:
        return None
    return component.model_copy(update={"method": "panel_component"})


def resolve(raw: str | None) -> LabMatch:
    """Resolve a written name to a panel or a single test.

    Three passes, each stricter than guessing. **The threshold is never
    lowered** -- what is added here is parsing of how laboratories actually
    print a line, not tolerance for a weak match:

    1. The name as written.
    2. With a parenthetical component list removed, so "Thyroid Profile
       (T3, T4, TSH)" is looked up as the panel it names.
    3. Split on a panel/component separator, so "Lipid Profile — HDL" resolves
       to HDL, which is what that line actually bills.

    Anything still unmatched stays unresolved. A mis-resolved panel silently
    changes which tests are considered ordered, which is worse than admitting
    a line could not be read.
    """
    if raw is None:
        return UNRESOLVED

    direct = _lookup(normalise(raw))
    if direct.resolved:
        return direct

    without_parens = _PARENTHETICAL.sub(" ", raw)
    if without_parens != raw:
        stripped = _lookup(normalise(without_parens))
        if stripped.resolved:
            return stripped.model_copy(update={"method": f"{stripped.method}_parenthetical"})

    parts = [part for part in _SEPARATOR.split(without_parens) if part.strip()]
    if len(parts) >= 2:
        resolved = [_lookup(normalise(part)) for part in parts]
        panels = [m for m in resolved if m.kind == "panel"]
        tests = [m for m in resolved if m.kind == "test"]
        if panels and tests:
            component = _component_of(panels[0], tests[0])
            if component is not None:
                return component
        # Only one side is recognisable. A panel with an unreadable component
        # is NOT the whole panel -- claiming it would satisfy an order from a
        # single line -- and an orphan component keeps its own identity.
        if tests and not panels:
            return tests[0].model_copy(update={"method": "component_only"})

    return UNRESOLVED


def panel_names() -> tuple[str, ...]:
    return tuple(PANELS.keys())
