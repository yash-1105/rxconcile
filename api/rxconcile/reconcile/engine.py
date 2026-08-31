"""The reconciliation engine.

**No LLM calls in this module.** Every verdict, finding and score is produced by
deterministic Python from already-extracted data. The model turns pixels into
structured data; this decides whether two documents agree. Those stages are
separate, always.

Four steps:

1. **Pair** prescribed items to billed items by composite similarity, assigned
   globally with :func:`scipy.optimize.linear_sum_assignment` so the result is
   optimal and reproducible rather than dependent on iteration order.
2. **Apply rules**, each emitting a :class:`Finding` with a stable rule code and
   a severity.
3. **Decide the verdict**, checking inconclusive first.
4. **Score**, or decline to score when nothing was reliably measured.

Findings describe what the documents say and where they differ. Nothing here
makes a clinical judgement.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any, Final, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

from rxconcile.models import (
    BilledItem,
    CanonicalMatch,
    Finding,
    MatchedPair,
    PharmacyBill,
    PrescribedItem,
    Prescription,
    ReconciliationResult,
    Severity,
    Verdict,
)
from rxconcile.normalize import (
    CanonicalDrug,
    Strength,
    doses_per_day,
    duration_to_days,
    expected_quantity,
    is_positional,
    normalize_strength,
    parse_pack_size,
    resolve,
)
from rxconcile.normalize.drug_dictionary import DrugEntry, entries_for_salt
from rxconcile.normalize.matcher import entry_for
from rxconcile.normalize.units import strengths_equal
from rxconcile.reconcile._findings import finding, unavailable
from rxconcile.reconcile.arithmetic import check_arithmetic
from rxconcile.reconcile.history import HistoryScope, PriorScan, check_history
from rxconcile.reconcile.lab import reconcile_tests
from rxconcile.reconcile.reimbursement import assess
from rxconcile.validate import check_gstin, check_licence
from rxconcile.validate.gstin import state_in_address

logger: Final = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Tunables. All explicit, all testable.
# --------------------------------------------------------------------------

#: Composite similarity weights. They sum to 1.0.
WEIGHT_DRUG: Final[float] = 0.60
WEIGHT_STRENGTH: Final[float] = 0.25
WEIGHT_FORM: Final[float] = 0.15

#: A pair must score strictly above this to be accepted.
#:
#: Note 0.55 sits just below WEIGHT_DRUG, so a confident drug match alone pairs
#: two lines even when neither states a strength or form -- the common case on
#: real bills. Anything less than a drug match cannot reach it.
PAIR_THRESHOLD: Final[float] = 0.55

#: Billed quantity above expectation by more than this fraction is an excess.
QUANTITY_EXCESS_TOLERANCE: Final[float] = 0.20

#: Below this fuzzy similarity, two patient names are treated as different.
NAME_SIMILARITY_THRESHOLD: Final[float] = 75.0

#: A bill this many days after the prescription is anomalous.
MAX_BILL_LAG_DAYS: Final[int] = 30

#: Verdict thresholds, per docs/ENGINE_SPEC.md.
MIN_MEAN_DRUG_AGREEMENT: Final[float] = 0.67
MAX_NULL_DRUG_NAME_SHARE: Final[float] = 0.5

SCORE_PENALTY_CRITICAL: Final[int] = 25
SCORE_PENALTY_WARNING: Final[int] = 8

#: Forms treated as non-medicine lines on a bill.
_NON_MEDICINE_FORMS: Final[frozenset[str]] = frozenset({"other", "service", "consumable"})

#: Words that identify a billed line as something other than a medicine.
#:
#: Only used on lines the drug dictionary did NOT resolve. A line that resolves
#: to a real medicine is a medicine, whatever words surround it -- "Zinc" in a
#: supplement name must never reclassify a prescribed zinc tablet.
_NON_MEDICINE_WORDS: Final[frozenset[str]] = frozenset({
    # Charges and services
    "delivery", "shipping", "courier", "handling", "service", "packing", "freight",
    "consultation", "registration", "convenience",
    # Devices and consumables
    "syringe", "needle", "glucometer", "lancet", "strip", "strips", "thermometer",
    "bandage", "gauze", "cotton", "mask", "gloves", "sanitizer", "sanitiser",
    "nebulizer", "nebuliser", "catheter", "diaper", "diapers", "wipes",
    "bp monitor", "oximeter", "crepe", "elastic",
    # Cosmetics and toiletries
    "soap", "shampoo", "lotion", "moisturizer", "moisturiser", "sunscreen",
    "toothpaste", "toothbrush", "talc", "powder puff", "lip balm", "face wash",
    # Supplements and food
    "protein", "supplement", "multivitamin", "health drink", "nutrition",
    "sanitary", "napkin",
})

_FORM_SYNONYMS: Final[dict[str, str]] = {
    "tab": "tablet", "tabs": "tablet", "tablet": "tablet", "tablets": "tablet",
    "cap": "capsule", "caps": "capsule", "capsule": "capsule", "capsules": "capsule",
    "syp": "syrup", "syrup": "syrup", "susp": "syrup", "suspension": "syrup",
    "liquid": "syrup", "solution": "solution",
    "inj": "injection", "injection": "injection", "vial": "injection",
    "ampoule": "injection",
    "oint": "ointment", "ointment": "ointment", "cream": "cream", "gel": "gel",
    "drop": "drops", "drops": "drops",
    "inhaler": "inhaler", "rotacap": "inhaler", "respule": "inhaler",
    "sachet": "sachet", "powder": "sachet",
}


def _norm_form(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    return _FORM_SYNONYMS.get(key, key)


def _strength_of(item: PrescribedItem | BilledItem) -> Strength | None:
    return normalize_strength(item.strength_value, item.strength_unit)


def _drug_text(item: PrescribedItem | BilledItem) -> str:
    """Prefer the extracted drug name, falling back to the transcribed line."""
    return item.drug_name or item.raw_text


# --------------------------------------------------------------------------
# Step 1 -- pairing
# --------------------------------------------------------------------------


def drug_component(left: CanonicalDrug, right: CanonicalDrug) -> float:
    """1.0 when both resolve to the same drug or the same salt, else 0.0.

    An unresolved drug scores 0. It deliberately does **not** fall back to raw
    string similarity: two illegible lines that happen to look alike are not
    evidence that the same medicine was dispensed.

    A brand match and a salt match score identically. They are therapeutically
    the same medicine, and pairing them is what lets BRAND_SUBSTITUTION report
    the brand difference rather than the line going unmatched.
    """
    if not left.resolved or not right.resolved:
        return 0.0
    if left.name is not None and left.name == right.name:
        return 1.0
    if left.salt is not None and left.salt == right.salt:
        return 1.0
    return 0.0


def strength_component(left: Strength | None, right: Strength | None) -> float:
    """1.0 when both strengths are present and equal, else 0.0.

    A missing strength on either side scores 0, not a free pass: absent data is
    not agreement.
    """
    if left is None or right is None:
        return 0.0
    return 1.0 if strengths_equal(left, right) else 0.0


def form_component(left: str | None, right: str | None) -> float:
    """1.0 when both forms are present and equal after normalisation."""
    if left is None or right is None:
        return 0.0
    return 1.0 if left == right else 0.0


def similarity(
    prescribed: PrescribedItem,
    billed: BilledItem,
    rx_drug: CanonicalDrug,
    bill_drug: CanonicalDrug,
) -> float:
    """Composite similarity in 0-1."""
    return (
        WEIGHT_DRUG * drug_component(rx_drug, bill_drug)
        + WEIGHT_STRENGTH * strength_component(_strength_of(prescribed), _strength_of(billed))
        + WEIGHT_FORM * form_component(_norm_form(prescribed.form), _norm_form(billed.form))
    )


def build_similarity_matrix(
    prescribed: Sequence[PrescribedItem],
    billed: Sequence[BilledItem],
    rx_drugs: Sequence[CanonicalDrug],
    bill_drugs: Sequence[CanonicalDrug],
) -> np.ndarray[Any, np.dtype[np.float64]]:
    matrix = np.zeros((len(prescribed), len(billed)), dtype=np.float64)
    for row, rx_item in enumerate(prescribed):
        for column, bill_item in enumerate(billed):
            matrix[row, column] = similarity(
                rx_item, bill_item, rx_drugs[row], bill_drugs[column]
            )
    return matrix


def pair_items(
    prescription: Prescription,
    bill: PharmacyBill,
    rx_drugs: Sequence[CanonicalDrug],
    bill_drugs: Sequence[CanonicalDrug],
) -> tuple[list[MatchedPair], list[str], list[str]]:
    """Assign prescribed to billed items globally.

    Returns ``(pairs, unmatched_prescribed_ids, unmatched_billed_ids)``.

    A globally optimal assignment is used rather than greedy nearest-match so
    that one line cannot claim a partner a different line needed more, and so
    the result does not depend on iteration order.
    """
    rx_items, bill_items = prescription.items, bill.items
    if not rx_items or not bill_items:
        return (
            [],
            [item.item_id for item in rx_items],
            [item.item_id for item in bill_items],
        )

    matrix = build_similarity_matrix(rx_items, bill_items, rx_drugs, bill_drugs)
    rows, columns = linear_sum_assignment(matrix, maximize=True)

    pairs: list[MatchedPair] = []
    matched_rx: set[int] = set()
    matched_bill: set[int] = set()
    for row, column in zip(rows.tolist(), columns.tolist(), strict=True):
        score = float(matrix[row, column])
        if score > PAIR_THRESHOLD:
            pairs.append(
                MatchedPair(
                    prescribed_id=rx_items[row].item_id,
                    billed_id=bill_items[column].item_id,
                    similarity=round(score, 4),
                )
            )
            matched_rx.add(row)
            matched_bill.add(column)

    unmatched_rx = [
        item.item_id for index, item in enumerate(rx_items) if index not in matched_rx
    ]
    unmatched_bill = [
        item.item_id for index, item in enumerate(bill_items) if index not in matched_bill
    ]
    return pairs, unmatched_rx, unmatched_bill


# --------------------------------------------------------------------------
# Step 2 -- rules
# --------------------------------------------------------------------------


#: Findings are built by the shared constructors so the medicine rules and the
#: lab rules cannot drift apart. Aliased rather than renamed at every call site.
_finding = finding
_unavailable = unavailable


def _schedule_entry(drug: CanonicalDrug) -> DrugEntry | None:
    """Dictionary entry behind a match, resolving through the salt if need be.

    A salt-level match carries no ``source`` brand, so ``entry_for`` returns None
    and SCHEDULE_H_UNBACKED was silently inert whenever a bill printed a generic
    name. Falling back to the salt keeps the rule alive; the most restrictive
    schedule among the matching brands is used, since dispensing rules follow the
    molecule rather than the brand.
    """
    entry = entry_for(drug)
    if entry is not None:
        return entry
    if drug.salt is None:
        return None
    candidates = entries_for_salt(drug.salt)
    if not candidates:
        return None
    order = {"X": 0, "H1": 1, "H": 2, "OTC": 3}
    return min(candidates, key=lambda item: order.get(item.schedule, 9))


def _is_non_medicine(item: BilledItem, drug: CanonicalDrug) -> bool:
    form = _norm_form(item.form)
    return form in _NON_MEDICINE_FORMS or (not drug.resolved and form is None)


def classify_line(item: BilledItem, drug: CanonicalDrug) -> str:
    """``medicine``, ``non_medicine`` or ``unclassified``.

    Three states on purpose. **Unclassified is a valid answer**: guessing that
    an unrecognised line is a cosmetic would quietly drop a real medicine out
    of reimbursement, which is the worst outcome available here.

    The dictionary wins over the keyword list. A line that resolves to a real
    medicine is a medicine whatever words surround it.
    """
    if drug.resolved:
        return "medicine"

    form = _norm_form(item.form)
    text = f"{item.drug_name or ''} {item.raw_text}".lower()
    if any(word in text for word in _NON_MEDICINE_WORDS):
        return "non_medicine"
    if form in _NON_MEDICINE_FORMS:
        return "non_medicine"
    if form is not None:
        # A stated dosage form with no dictionary entry is most likely a
        # medicine this build does not know, not a cosmetic.
        return "medicine"
    return "unclassified"


def _pair_rules(
    prescribed: PrescribedItem,
    billed: BilledItem,
    rx_drug: CanonicalDrug,
    bill_drug: CanonicalDrug,
) -> list[Finding]:
    findings: list[Finding] = []
    refs = {"prescribed_ref": prescribed.item_id, "billed_ref": billed.item_id}

    rx_strength, bill_strength = _strength_of(prescribed), _strength_of(billed)
    strengths_match = False
    strength_known = rx_strength is not None and bill_strength is not None
    if not strength_known:
        missing = [
            side
            for side, value in (
                ("prescribed strength", rx_strength),
                ("billed strength", bill_strength),
            )
            if value is None
        ]
        findings.append(_unavailable("strength", missing, **refs))
    if rx_strength is not None and bill_strength is not None:
        detail = {
            "expected": {"value": rx_strength.value, "unit": rx_strength.unit},
            "found": {"value": bill_strength.value, "unit": bill_strength.unit},
        }
        units_stated = rx_strength.unit is not None and bill_strength.unit is not None
        values_equal = abs(rx_strength.value - bill_strength.value) < 1e-6

        if units_stated:
            strengths_match = strengths_equal(rx_strength, bill_strength)
            if not strengths_match:
                findings.append(
                    _finding(
                        "STRENGTH_MISMATCH", "critical",
                        f"Prescribed strength {rx_strength} does not match billed "
                        f"strength {bill_strength}.",
                        **refs, detail=detail,
                    )
                )
        elif values_equal:
            # One side does not print a unit -- "DOLO 650" against "CALPOL 650MG".
            # The extractor is right not to invent the missing unit, so the numbers
            # are all there is to compare. Equal numbers are not a discrepancy, and
            # calling them one would be a critical false positive on a correct
            # extraction. The gap is recorded instead.
            strengths_match = True
            findings.append(
                _finding(
                    "STRENGTH_UNIT_UNSTATED", "info",
                    f"Both documents show {rx_strength.value:g}, but a unit is printed "
                    "on only one of them, so the strengths could not be compared in "
                    "full. The numbers agree.",
                    **refs, detail=detail,
                )
            )
        else:
            findings.append(
                _finding(
                    "STRENGTH_MISMATCH", "critical",
                    f"Prescribed strength {rx_strength} does not match billed "
                    f"strength {bill_strength}.",
                    **refs, detail=detail,
                )
            )

    rx_form, bill_form = _norm_form(prescribed.form), _norm_form(billed.form)
    if rx_form is None or bill_form is None:
        findings.append(
            _unavailable(
                "dosage form",
                [
                    side
                    for side, value in (("prescribed form", rx_form), ("billed form", bill_form))
                    if value is None
                ],
                **refs,
            )
        )
    if rx_form is not None and bill_form is not None and rx_form != bill_form:
        findings.append(
            _finding(
                "FORM_MISMATCH", "warning",
                f"Prescribed as {rx_form}, billed as {bill_form}.",
                **refs, detail={"expected": rx_form, "found": bill_form},
            )
        )

    # Different brand, same salt: legal substitution in India.
    #
    # Deliberately NOT gated on strengths_match. Requiring a verified strength
    # meant an illegible strength silently suppressed the finding entirely, so a
    # substitution went unreported and unreported reads as nothing to see. Whether
    # the strength could be checked is recorded instead.
    strength_contradicts = any(f.rule_code == "STRENGTH_MISMATCH" for f in findings)
    if (
        rx_drug.resolved
        and bill_drug.resolved
        and rx_drug.name != bill_drug.name
        and rx_drug.salt == bill_drug.salt
        and not strength_contradicts
    ):
        findings.append(
            _finding(
                "BRAND_SUBSTITUTION", "info",
                f"Billed brand {bill_drug.name} differs from prescribed "
                f"{rx_drug.name}; same salt."
                + (
                    " Strength matches."
                    if strengths_match
                    else " The strength could not be verified on both documents."
                ),
                **refs,
                detail={
                    "prescribed_brand": rx_drug.name,
                    "billed_brand": bill_drug.name,
                    "salt": rx_drug.salt,
                    "strength_verified": strengths_match,
                },
            )
        )

    # A fuzzy match landing in a different therapeutic class is the signature of
    # a misread rather than a substitution.
    if rx_drug.resolved and bill_drug.resolved and "fuzzy" in {rx_drug.method, bill_drug.method}:
        rx_entry, bill_entry = entry_for(rx_drug), entry_for(bill_drug)
        if (
            rx_entry is not None
            and bill_entry is not None
            and rx_entry.therapeutic_class != bill_entry.therapeutic_class
        ):
            findings.append(
                _finding(
                    "SALT_DIFFERENT_CLASS", "critical",
                    f"Fuzzy-matched {rx_drug.name} and {bill_drug.name} belong to "
                    "different therapeutic classes; this is more likely a misreading "
                    "than a substitution.",
                    **refs,
                    detail={
                        "prescribed_class": rx_entry.therapeutic_class,
                        "billed_class": bill_entry.therapeutic_class,
                        "match_methods": [rx_drug.method, bill_drug.method],
                    },
                )
            )

    findings.extend(_quantity_rules(prescribed, billed, refs))
    return findings


#: Relative tolerance when checking a bill line's own arithmetic.
#: Loose enough to absorb rounding, tight enough that a pack-size factor of 10
#: never fits inside it.
PRICE_TOLERANCE_RATIO: Final[float] = 0.02


class QuantityBasis(BaseModel):
    """What a billed ``quantity`` counts, and how that was established."""

    model_config = ConfigDict(frozen=True)

    basis: Literal["pack", "unit"] | None = None
    method: Literal[
        "declared", "price_reconciled", "price_inconclusive", "no_price_data"
    ] = "no_price_data"


def _close(left: float, right: float) -> bool:
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) <= PRICE_TOLERANCE_RATIO * scale


def resolve_units_basis(billed: BilledItem, units_per_pack: int | None) -> QuantityBasis:
    """Decide whether ``quantity`` counts packs or units, or decline to.

    A basis stated on the bill wins outright. Otherwise the line's own
    arithmetic is consulted: if ``quantity x units_per_pack x unit_price``
    reconciles to ``line_total`` while ``quantity x unit_price`` does not, the
    rate is per dosage unit and the quantity therefore counts packs.

    **The converse does not resolve.** ``quantity x unit_price == line_total`` is
    equally consistent with units priced per unit and packs priced per pack, so
    it is reported as ``price_inconclusive`` rather than assumed. Discounts also
    push ``line_total`` below the gross figure, so a mismatch is treated as
    absence of evidence, never as evidence for the other reading.
    """
    if billed.units_basis is not None:
        return QuantityBasis(basis=billed.units_basis, method="declared")

    quantity, unit_price, line_total = billed.quantity, billed.unit_price, billed.line_total
    if quantity is None or unit_price is None or line_total is None or units_per_pack is None:
        return QuantityBasis(basis=None, method="no_price_data")

    total = float(line_total)
    as_stated = quantity * float(unit_price)
    as_packs = quantity * units_per_pack * float(unit_price)

    if _close(as_packs, total) and not _close(as_stated, total):
        return QuantityBasis(basis="pack", method="price_reconciled")
    return QuantityBasis(basis=None, method="price_inconclusive")


def _quantity_outcome(billed_units: float, expected: float) -> str | None:
    """Which quantity rule, if any, this reading would raise."""
    if billed_units < expected:
        return "QUANTITY_SHORT"
    if billed_units > expected * (1 + QUANTITY_EXCESS_TOLERANCE):
        return "QUANTITY_EXCESS"
    return None


def _quantity_rules(
    prescribed: PrescribedItem, billed: BilledItem, refs: dict[str, str]
) -> list[Finding]:
    """QUANTITY_SHORT / QUANTITY_EXCESS / QUANTITY_AMBIGUOUS, or nothing.

    Skips silently whenever the expectation cannot be computed. A null duration
    is not a discrepancy, and a rule fired against an absent expectation reports
    a difference from a number nobody wrote down.

    When the basis is unresolved the expectation is compared under **both**
    readings. The finding is emitted only if both readings raise the same one --
    the discrepancy is then real either way. If the readings disagree, nothing
    is asserted and QUANTITY_AMBIGUOUS records why.
    """
    days = prescribed.duration_days
    if days is None:
        days = duration_to_days(prescribed.duration_raw)
    per_day = doses_per_day(prescribed.frequency_raw)

    # dose_per_administration is NOT blanket-defaulted to 1.0. Substituting a dose
    # the page does not state is the fabrication these rules exist to catch: with a
    # true dose of 2, the old default silently turned a real QUANTITY_SHORT into a
    # clean pass.
    #
    # Positional notation is the one exception, and it is not an assumption. In
    # "1 - 0 - 1" the slots state the units taken at each time of day, and
    # doses_per_day already sums them to units per day, so the dose is carried by
    # the notation. Requiring a separate value there would both block the check
    # and double-count when one was supplied. A Latin code such as BD says nothing
    # about units per administration, so a missing dose really does block it.
    dose = prescribed.dose_per_administration
    if dose is None and is_positional(prescribed.frequency_raw):
        dose = 1.0
    expected = expected_quantity(per_day, days, dose)

    missing: list[str] = []
    if per_day is None:
        missing.append("a readable dosing frequency")
    if days is None:
        missing.append("a course duration in days")
    if dose is None:
        missing.append("the dose per administration (frequency is not positional)")
    if billed.quantity is None:
        missing.append("a billed quantity")

    pack = parse_pack_size(billed.pack_size)
    units_per_pack = pack.units_per_pack if pack is not None else None
    resolution = resolve_units_basis(billed, units_per_pack)
    if units_per_pack is None and resolution.basis != "unit":
        missing.append("a parseable pack size")

    if missing or expected is None or billed.quantity is None:
        return [
            _unavailable(
                "quantity",
                missing or ["a computable expected quantity"],
                **refs,
                note="Quantity was not compared, which is not the same as finding "
                "it correct.",
            )
        ]

    detail: dict[str, Any] = {
        "expected_units": expected,
        "billed_quantity": billed.quantity,
        "units_per_pack": units_per_pack,
        "pack_size": billed.pack_size,
        "duration_days": days,
        "units_basis": resolution.basis,
        "basis_method": resolution.method,
    }

    if resolution.basis == "unit":
        billed_units = billed.quantity
    elif resolution.basis == "pack":
        if units_per_pack is None:
            return [_unavailable("quantity", ["a parseable pack size"], **refs)]
        billed_units = billed.quantity * units_per_pack
    else:
        # Unresolved: compare under both readings and only assert what holds
        # under each.
        if units_per_pack is None:
            return [_unavailable("quantity", ["a parseable pack size"], **refs)]
        as_units = billed.quantity
        as_packs = billed.quantity * units_per_pack
        outcome_unit = _quantity_outcome(as_units, expected)
        outcome_pack = _quantity_outcome(as_packs, expected)
        detail["interpretations"] = {
            "as_units": {"billed_units": as_units, "outcome": outcome_unit},
            "as_packs": {"billed_units": as_packs, "outcome": outcome_pack},
        }
        if outcome_unit != outcome_pack:
            return [
                _finding(
                    "QUANTITY_AMBIGUOUS", "info",
                    f"Billed quantity {billed.quantity:g} against a pack of "
                    f"{units_per_pack} could mean {as_units:g} or {as_packs:g} units; "
                    f"the bill does not say which, and the two readings disagree "
                    f"(expected {expected:g}). No quantity discrepancy is asserted.",
                    **refs, detail=detail,
                )
            ]
        if outcome_unit is None:
            return []
        billed_units = as_packs
        detail["note"] = "Same outcome under both readings, so the discrepancy holds either way."

    detail["billed_units"] = billed_units
    outcome = _quantity_outcome(billed_units, expected)
    if outcome == "QUANTITY_SHORT":
        return [
            _finding(
                "QUANTITY_SHORT", "warning",
                f"Billed {billed_units:g} units against an expected {expected:g} "
                "for the prescribed course.",
                **refs, detail=detail,
            )
        ]
    if outcome == "QUANTITY_EXCESS":
        return [
            _finding(
                "QUANTITY_EXCESS", "warning",
                f"Billed {billed_units:g} units against an expected {expected:g}, "
                f"more than {QUANTITY_EXCESS_TOLERANCE:.0%} above.",
                **refs, detail=detail,
            )
        ]
    return []


def _unmatched_rules(
    prescription: Prescription,
    bill: PharmacyBill,
    unmatched_rx: Sequence[str],
    unmatched_bill: Sequence[str],
    rx_drugs: dict[str, CanonicalDrug],
    bill_drugs: dict[str, CanonicalDrug],
) -> list[Finding]:
    findings: list[Finding] = []
    rx_by_id = {item.item_id: item for item in prescription.items}
    bill_by_id = {item.item_id: item for item in bill.items}

    # An unmatched line on the other document that nobody could identify might be
    # the missing counterpart. While one exists, "this was not dispensed" is not
    # a claim the data supports, however clearly this side was read.
    unknown_bill = [i for i in unmatched_bill if not bill_drugs[i].resolved]
    unknown_rx = [i for i in unmatched_rx if not rx_drugs[i].resolved]

    # Lab bills and pharmacy bills are routinely separate documents. A bill that
    # carries lab lines and no medicines at all is a lab bill, and a lab bill is
    # not evidence that a prescribed medicine went undispensed -- the pharmacy
    # bill is simply a different piece of paper that was not uploaded.
    lab_only_bill = bool(bill.tests) and not bill.items

    for item_id in unmatched_rx:
        rx_line = rx_by_id[item_id]
        identified = rx_drugs[item_id].resolved
        confident = identified and not unknown_bill and not lab_only_bill
        # An unidentifiable line cannot support the claim that it was not
        # dispensed -- only that nobody could tell. Critical is reserved for a
        # drug we actually recognised.
        if confident:
            message = (
                f"Prescribed item {rx_line.drug_name or rx_line.raw_text!r} has no "
                "corresponding line on the bill."
            )
        elif not identified:
            message = (
                f"Prescribed line {rx_line.raw_text!r} could not be identified, so "
                "whether it was dispensed could not be determined."
            )
        elif lab_only_bill:
            message = (
                f"Prescribed item {rx_line.drug_name!r} does not appear on this bill, "
                "but the bill carries only lab tests and no medicines at all -- the "
                "pharmacy bill is a separate document and may not have been supplied."
            )
        else:
            message = (
                f"Prescribed item {rx_line.drug_name!r} was not matched to any billed "
                f"line, but {len(unknown_bill)} billed line(s) could not be identified "
                "and one of them may be it."
            )
        findings.append(
            _finding(
                "RX_NOT_BILLED",
                "critical" if confident else "warning",
                message,
                prescribed_ref=item_id,
                detail={
                    "drug_name": rx_line.drug_name,
                    "raw_text": rx_line.raw_text,
                    "identified": identified,
                    "unidentified_billed_lines": list(unknown_bill),
                    "lab_only_bill": lab_only_bill,
                },
            )
        )
        if not confident:
            findings.append(
                _unavailable(
                    "billed-counterpart",
                    ["an identifiable drug name on the prescription line"]
                    if not identified
                    else ["any medicine line on the bill"]
                    if lab_only_bill
                    else [f"identifiable drug names on {len(unknown_bill)} billed line(s)"],
                    prescribed_ref=item_id,
                )
            )

    for item_id in unmatched_bill:
        bill_line = bill_by_id[item_id]
        drug = bill_drugs[item_id]
        non_medicine = _is_non_medicine(bill_line, drug)
        identified = drug.resolved
        confident_bill = identified and not unknown_rx
        severity: Severity = (
            "info" if non_medicine else ("critical" if confident_bill else "warning")
        )
        findings.append(
            _finding(
                "BILL_NOT_PRESCRIBED",
                severity,
                f"Billed line {bill_line.drug_name or bill_line.raw_text!r} has no "
                "corresponding line on the prescription."
                + (" Recorded as a non-medicine line." if non_medicine else ""),
                billed_ref=item_id,
                detail={
                    "drug_name": bill_line.drug_name,
                    "raw_text": bill_line.raw_text,
                    "form": bill_line.form,
                    "non_medicine": non_medicine,
                    "identified": identified,
                    "unidentified_prescribed_lines": list(unknown_rx),
                    "line_total": (
                        str(bill_line.line_total) if bill_line.line_total is not None else None
                    ),
                },
            )
        )
        if not confident_bill and not non_medicine:
            findings.append(
                _unavailable(
                    "prescription-counterpart",
                    ["an identifiable drug name on the billed line"]
                    if not identified
                    else [f"identifiable drug names on {len(unknown_rx)} prescribed line(s)"],
                    billed_ref=item_id,
                )
            )

        entry = _schedule_entry(drug)
        if entry is not None and entry.requires_prescription:
            findings.append(
                _finding(
                    "SCHEDULE_H_UNBACKED", "critical",
                    f"Schedule {entry.schedule} item {entry.brand_name} was billed "
                    "with no prescription line backing it.",
                    billed_ref=item_id,
                    detail={
                        "brand": entry.brand_name,
                        "salt": entry.salt_composition,
                        "schedule": entry.schedule,
                    },
                )
            )
    return findings


def _document_rules(
    prescription: Prescription,
    bill: PharmacyBill,
    bill_drugs: dict[str, CanonicalDrug],
) -> list[Finding]:
    findings: list[Finding] = []

    # Two billed lines resolving to the same salt.
    by_salt: dict[str, list[str]] = {}
    for item in bill.items:
        drug = bill_drugs[item.item_id]
        if drug.resolved and drug.salt is not None:
            by_salt.setdefault(drug.salt, []).append(item.item_id)
    for salt, item_ids in by_salt.items():
        if len(item_ids) > 1:
            findings.append(
                _finding(
                    "DUPLICATE_THERAPY", "warning",
                    f"{len(item_ids)} billed lines resolve to the same salt ({salt}).",
                    billed_ref=item_ids[0],
                    detail={"salt": salt, "billed_refs": item_ids},
                )
            )

    unresolved_bill_lines = [
        item.item_id for item in bill.items if not bill_drugs[item.item_id].resolved
    ]
    if unresolved_bill_lines:
        findings.append(
            _unavailable(
                "duplicate-therapy",
                [f"{len(unresolved_bill_lines)} billed line(s) that could not be identified"],
                note="Duplicate detection compares salts, so unidentified lines are "
                "excluded from it.",
            )
        )

    if not prescription.patient_name or not bill.patient_name:
        findings.append(
            _unavailable(
                "patient name",
                [
                    side
                    for side, value in (
                        ("a patient name on the prescription", prescription.patient_name),
                        ("a patient name on the bill", bill.patient_name),
                    )
                    if not value
                ],
            )
        )
    if prescription.patient_name and bill.patient_name:
        score = float(fuzz.token_set_ratio(prescription.patient_name, bill.patient_name))
        if score < NAME_SIMILARITY_THRESHOLD:
            findings.append(
                _finding(
                    "PATIENT_NAME_MISMATCH", "warning",
                    f"Prescription names {prescription.patient_name!r}; bill names "
                    f"{bill.patient_name!r}.",
                    detail={
                        "prescription_name": prescription.patient_name,
                        "bill_name": bill.patient_name,
                        "similarity": round(score, 1),
                    },
                )
            )

    if not prescription.date_issued or not bill.bill_date:
        # Handwritten dates are designed to come back null when ambiguous, so on
        # most real prescriptions this check has never once run.
        findings.append(
            _unavailable(
                "document date",
                [
                    side
                    for side, value in (
                        ("a resolvable prescription date", prescription.date_issued),
                        ("a resolvable bill date", bill.bill_date),
                    )
                    if not value
                ],
                note="An ambiguous handwritten date is deliberately left null rather "
                "than guessed, so this check is often unavailable.",
            )
        )
    if prescription.date_issued and bill.bill_date:
        delta = (bill.bill_date - prescription.date_issued).days
        if delta < 0 or delta > MAX_BILL_LAG_DAYS:
            findings.append(
                _finding(
                    "DATE_ANOMALY", "warning",
                    f"Bill is dated {bill.bill_date.isoformat()} against a "
                    f"prescription dated {prescription.date_issued.isoformat()} "
                    f"({delta} days).",
                    detail={
                        "prescription_date": prescription.date_issued.isoformat(),
                        "bill_date": bill.bill_date.isoformat(),
                        "days_between": delta,
                        "max_lag_days": MAX_BILL_LAG_DAYS,
                    },
                )
            )

    for document, label in ((prescription, "prescription"), (bill, "bill")):
        counts = document.run_item_counts
        if len(counts) < 2:
            findings.append(
                _unavailable(
                    f"{label} item-count stability",
                    ["more than one extraction run"],
                    note="A single run cannot reveal whether a line appears "
                    "intermittently.",
                )
            )
        if len(set(counts)) > 1:
            findings.append(
                _finding(
                    "ITEM_COUNT_UNSTABLE", "critical",
                    f"The {label} returned different item counts across extraction "
                    f"runs ({counts}); {len(document.unstable_lines)} line(s) were "
                    "not seen by every run.",
                    # Both refs stay null: an intermittent line has no item_id in
                    # the canonical extraction, and referencing one would fail
                    # the referential-integrity validator.
                    detail={
                        "document": label,
                        "run_item_counts": list(counts),
                        "unstable_lines": list(document.unstable_lines),
                    },
                )
            )
    return findings


def _bill_integrity_findings(
    bill: PharmacyBill, bill_drugs: dict[str, CanonicalDrug]
) -> list[Finding]:
    """Checks about the bill as a document, independent of the prescription."""
    findings: list[Finding] = []

    # ---- GSTIN ---------------------------------------------------------
    gstin = check_gstin(bill.gstin)
    if not gstin.present:
        findings.append(
            _unavailable(
                "GSTIN format",
                ["a GSTIN on the bill"],
                note="No GST number was printed, so its format could not be checked.",
            )
        )
    elif not gstin.well_formed:
        findings.append(
            _finding(
                "GSTIN_INVALID", "warning",
                f"The printed GST number {gstin.normalised} is not a valid GSTIN "
                f"format: {gstin.reason}. This is a check of the number's structure "
                "only -- no registry was consulted.",
                detail={
                    "printed": gstin.raw,
                    "normalised": gstin.normalised,
                    "reason": gstin.reason,
                    "expected_check_digit": gstin.expected_check_digit,
                    "scope": "format_and_checksum_only",
                },
            )
        )
    else:
        # Well-formed. A state disagreement is never more than informational:
        # a chain legitimately bills from a state it is not addressed in.
        address_state = state_in_address(bill.pharmacy_address)
        if address_state and gstin.state_name and address_state != gstin.state_name:
            findings.append(
                _finding(
                    "GSTIN_STATE_MISMATCH", "info",
                    f"The GSTIN is registered in {gstin.state_name} but the address "
                    f"printed on the bill is in {address_state}. This is common for a "
                    "chain billing from another state and is not itself a problem.",
                    detail={
                        "gstin_state": gstin.state_name,
                        "address_state": address_state,
                        "state_code": gstin.state_code,
                    },
                )
            )

    # ---- drug licence --------------------------------------------------
    licence = check_licence(bill.pharmacy_licence_no)
    if not licence.present:
        findings.append(
            _finding(
                "LICENCE_ABSENT", "warning",
                "No drug licence number is printed on this bill. A retail pharmacy "
                "invoice is required to carry one.",
                detail={"note": licence.note},
            )
        )

    # ---- non-medicine lines --------------------------------------------
    for item in bill.items:
        if classify_line(item, bill_drugs[item.item_id]) != "non_medicine":
            continue
        findings.append(
            _finding(
                "NON_MEDICINE_ITEM", "info",
                f"{item.drug_name or item.raw_text} is not a medicine and is usually "
                "outside the scope of a medical reimbursement.",
                billed_ref=item.item_id,
                detail={
                    "line": item.drug_name or item.raw_text,
                    "line_total": str(item.line_total) if item.line_total else None,
                },
            )
        )

    return findings


def _low_agreement_findings(prescription: Prescription, bill: PharmacyBill) -> list[Finding]:
    """One finding per item that needs review, never one per field."""
    findings: list[Finding] = []

    def review_finding(
        item: PrescribedItem | BilledItem, *, prescribed: bool
    ) -> Finding | None:
        agreement = item.agreement
        if not agreement:
            return None
        shaky = {field: ratio for field, ratio in agreement.items() if ratio < 1.0}
        if not shaky:
            return None
        nulled = [field for field in shaky if getattr(item, field, None) is None]
        return _finding(
            "LOW_CONFIDENCE_FIELD", "info",
            f"{len(shaky)} field(s) on this item did not agree across "
            "extraction runs and need review.",
            prescribed_ref=item.item_id if prescribed else None,
            billed_ref=None if prescribed else item.item_id,
            detail={
                "agreement": dict(sorted(shaky.items())),
                "min_agreement": min(shaky.values()),
                "nulled_fields": sorted(nulled),
            },
        )

    for rx_line in prescription.items:
        finding = review_finding(rx_line, prescribed=True)
        if finding is not None:
            findings.append(finding)
    for bill_line in bill.items:
        finding = review_finding(bill_line, prescribed=False)
        if finding is not None:
            findings.append(finding)
    return findings


# --------------------------------------------------------------------------
# Step 3 -- verdict
# --------------------------------------------------------------------------


def _null_drug_share(prescription: Prescription) -> float:
    if not prescription.items:
        return 0.0
    nulls = sum(1 for item in prescription.items if item.drug_name is None)
    return nulls / len(prescription.items)


def _mean_drug_agreement(prescription: Prescription) -> float | None:
    ratios = [
        item.agreement["drug_name"]
        for item in prescription.items
        if item.agreement and "drug_name" in item.agreement
    ]
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def _counts_unstable(prescription: Prescription, bill: PharmacyBill) -> bool:
    return len(set(prescription.run_item_counts)) > 1 or len(set(bill.run_item_counts)) > 1


def is_inconclusive(prescription: Prescription, bill: PharmacyBill) -> tuple[bool, list[str]]:
    """Whether the documents could be read reliably enough to judge, and why not."""
    reasons: list[str] = []
    share = _null_drug_share(prescription)
    if share > MAX_NULL_DRUG_NAME_SHARE:
        reasons.append(
            f"{share:.0%} of prescribed items have no legible drug name "
            f"(threshold {MAX_NULL_DRUG_NAME_SHARE:.0%})"
        )
    mean_agreement = _mean_drug_agreement(prescription)
    if mean_agreement is not None and mean_agreement < MIN_MEAN_DRUG_AGREEMENT:
        reasons.append(
            f"mean drug_name agreement across runs is {mean_agreement:.2f} "
            f"(threshold {MIN_MEAN_DRUG_AGREEMENT})"
        )
    if _counts_unstable(prescription, bill):
        reasons.append(
            f"item counts differed across extraction runs "
            f"(prescription {prescription.run_item_counts}, bill {bill.run_item_counts})"
        )
    return bool(reasons), reasons


def decide_verdict(
    prescription: Prescription, bill: PharmacyBill, findings: Sequence[Finding]
) -> tuple[Verdict, list[str]]:
    """Verdict, checked in order: inconclusive, mismatch, warnings, match."""
    inconclusive, reasons = is_inconclusive(prescription, bill)
    if inconclusive:
        return "inconclusive", reasons
    if any(finding.severity == "critical" for finding in findings):
        return "mismatch", []
    if any(finding.severity == "warning" for finding in findings):
        return "match_with_warnings", []
    return "match", []


# --------------------------------------------------------------------------
# Step 4 -- score
# --------------------------------------------------------------------------


def compute_score(verdict: Verdict, findings: Sequence[Finding]) -> float | None:
    """Score 0-100, or None when the verdict is inconclusive.

    None rather than 0: a number implies something was measured, and an
    inconclusive result measured nothing reliably. Zero would read as
    "measured, terrible" instead of "not measurable".

    Info findings do not affect the score.
    """
    if verdict == "inconclusive":
        return None
    criticals = sum(1 for finding in findings if finding.severity == "critical")
    warnings = sum(1 for finding in findings if finding.severity == "warning")
    raw = 100 - SCORE_PENALTY_CRITICAL * criticals - SCORE_PENALTY_WARNING * warnings
    return float(max(0, raw))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def reconcile(
    prescription: Prescription,
    bill: PharmacyBill,
    *,
    processing_ms: int | None = None,
    priors: Sequence[PriorScan] | None = None,
    history_scope: HistoryScope | None = None,
) -> ReconciliationResult:
    """Reconcile a prescription against a bill. Pure, deterministic, no LLM.

    Args:
        prescription: extracted prescription.
        bill: extracted pharmacy bill.
        processing_ms: override the measured wall time, for reproducible tests.
        priors: scans already on record, ALREADY narrowed to what the caller may
            see. Passed as plain data so this module never touches a database
            and never widens its own visibility. Omit to skip the history
            checks entirely; pass an empty list to run them against no history,
            which reports that they could not run.
        history_scope: what the caller was able to compare against, carried into
            every history finding so a report cannot imply the whole record was
            searched.

    Returns:
        A :class:`ReconciliationResult`. Findings are always computed in full,
        including under an inconclusive verdict -- suppressing them would
        discard information a reviewer needs. Under inconclusive they are
        provisional observations about a document that could not be read
        reliably, never assertions.
    """
    started = time.monotonic()

    rx_drugs = [resolve(_drug_text(item)) for item in prescription.items]
    bill_drugs = [resolve(_drug_text(item)) for item in bill.items]
    rx_by_id = {item.item_id: drug for item, drug in zip(prescription.items, rx_drugs, strict=True)}
    bill_by_id = {item.item_id: drug for item, drug in zip(bill.items, bill_drugs, strict=True)}

    pairs, unmatched_rx, unmatched_bill = pair_items(prescription, bill, rx_drugs, bill_drugs)

    rx_items = {item.item_id: item for item in prescription.items}
    bill_items = {item.item_id: item for item in bill.items}

    # The matcher resolved these on the way in; report them rather than letting
    # a salt escape only when a particular finding happens to fire.
    canonical: list[CanonicalMatch] = []
    sides: tuple[tuple[Literal["prescription", "bill"], dict[str, CanonicalDrug]], ...] = (
        ("prescription", rx_by_id),
        ("bill", bill_by_id),
    )
    for side, table in sides:
        canonical.extend(
            CanonicalMatch(
                item_id=item_id, side=side, name=drug.name, salt=drug.salt,
                match_score=drug.match_score, method=drug.method,
            )
            for item_id, drug in table.items()
        )

    findings: list[Finding] = []
    for pair in pairs:
        findings.extend(
            _pair_rules(
                rx_items[pair.prescribed_id],
                bill_items[pair.billed_id],
                rx_by_id[pair.prescribed_id],
                bill_by_id[pair.billed_id],
            )
        )
    findings.extend(
        _unmatched_rules(
            prescription, bill, unmatched_rx, unmatched_bill, rx_by_id, bill_by_id
        )
    )
    # Lab tests run through their own pairing, then join the same findings list.
    # Nothing downstream distinguishes them: the verdict and the score treat a
    # critical test finding exactly as they treat a critical medicine finding.
    lab = reconcile_tests(prescription, bill)
    findings.extend(lab.findings)

    findings.extend(_document_rules(prescription, bill, bill_by_id))
    if priors is not None:
        findings.extend(
            check_history(
                prescription, bill, canonical, list(priors),
                history_scope or HistoryScope(scans_compared=len(priors)),
            )
        )
    # Checks about the bill as a document: does it add up, and does it carry
    # the identifiers a pharmacy invoice is required to carry.
    findings.extend(check_arithmetic(bill))
    findings.extend(_bill_integrity_findings(bill, bill_by_id))
    findings.extend(_low_agreement_findings(prescription, bill))

    verdict, reasons = decide_verdict(prescription, bill, findings)
    if verdict == "inconclusive":
        findings.append(
            _finding(
                "ILLEGIBLE_RX", "info",
                "This document could not be read reliably; findings below are "
                "provisional observations, not assertions. "
                + "; ".join(reasons),
                detail={"reasons": reasons},
            )
        )

    score = compute_score(verdict, findings)
    elapsed = processing_ms if processing_ms is not None else int(
        (time.monotonic() - started) * 1000
    )

    logger.info(
        "reconciled: verdict=%s score=%s pairs=%d test_pairs=%d findings=%d",
        verdict, score, len(pairs), len(lab.matched), len(findings),
    )
    return ReconciliationResult(
        verdict=verdict,
        score=score,
        findings=findings,
        matched_pairs=pairs,
        unmatched_prescribed=unmatched_rx,
        unmatched_billed=unmatched_bill,
        canonical=canonical,
        reimbursement=assess(
            bill,
            findings,
            matched_billed_ids={p.billed_id for p in pairs}
            | {p.billed_id for p in lab.matched},
        ),
        matched_tests=lab.matched,
        unmatched_prescribed_tests=lab.unmatched_prescribed,
        unmatched_billed_tests=lab.unmatched_billed,
        prescription=prescription,
        bill=bill,
        processing_ms=elapsed,
    )
