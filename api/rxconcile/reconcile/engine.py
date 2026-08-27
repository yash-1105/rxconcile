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
    normalize_strength,
    parse_pack_size,
    resolve,
)
from rxconcile.normalize.matcher import entry_for
from rxconcile.normalize.units import strengths_equal

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


def _finding(
    rule_code: str,
    severity: Severity,
    message: str,
    *,
    prescribed_ref: str | None = None,
    billed_ref: str | None = None,
    detail: dict[str, Any] | None = None,
) -> Finding:
    return Finding(
        rule_code=rule_code,
        severity=severity,
        message=message,
        prescribed_ref=prescribed_ref,
        billed_ref=billed_ref,
        detail=detail or {},
    )


def _is_non_medicine(item: BilledItem, drug: CanonicalDrug) -> bool:
    form = _norm_form(item.form)
    return form in _NON_MEDICINE_FORMS or (not drug.resolved and form is None)


def _pair_rules(
    prescribed: PrescribedItem,
    billed: BilledItem,
    rx_drug: CanonicalDrug,
    bill_drug: CanonicalDrug,
) -> list[Finding]:
    findings: list[Finding] = []
    refs = {"prescribed_ref": prescribed.item_id, "billed_ref": billed.item_id}

    rx_strength, bill_strength = _strength_of(prescribed), _strength_of(billed)
    strengths_match = (
        rx_strength is not None
        and bill_strength is not None
        and strengths_equal(rx_strength, bill_strength)
    )
    if rx_strength is not None and bill_strength is not None and not strengths_match:
        findings.append(
            _finding(
                "STRENGTH_MISMATCH", "critical",
                f"Prescribed strength {rx_strength} does not match billed strength "
                f"{bill_strength}.",
                **refs,
                detail={
                    "expected": {"value": rx_strength.value, "unit": rx_strength.unit},
                    "found": {"value": bill_strength.value, "unit": bill_strength.unit},
                },
            )
        )

    rx_form, bill_form = _norm_form(prescribed.form), _norm_form(billed.form)
    if rx_form is not None and bill_form is not None and rx_form != bill_form:
        findings.append(
            _finding(
                "FORM_MISMATCH", "warning",
                f"Prescribed as {rx_form}, billed as {bill_form}.",
                **refs, detail={"expected": rx_form, "found": bill_form},
            )
        )

    # Different brand, same salt and strength: legal substitution in India.
    if (
        rx_drug.resolved
        and bill_drug.resolved
        and rx_drug.name != bill_drug.name
        and rx_drug.salt == bill_drug.salt
        and strengths_match
    ):
        findings.append(
            _finding(
                "BRAND_SUBSTITUTION", "info",
                f"Billed brand {bill_drug.name} differs from prescribed "
                f"{rx_drug.name}; same salt and strength.",
                **refs,
                detail={
                    "prescribed_brand": rx_drug.name,
                    "billed_brand": bill_drug.name,
                    "salt": rx_drug.salt,
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
    expected = expected_quantity(
        doses_per_day(prescribed.frequency_raw), days, prescribed.dose_per_administration or 1.0
    )
    if expected is None or billed.quantity is None:
        return []

    pack = parse_pack_size(billed.pack_size)
    units_per_pack = pack.units_per_pack if pack is not None else None
    resolution = resolve_units_basis(billed, units_per_pack)

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
            return []
        billed_units = billed.quantity * units_per_pack
    else:
        # Unresolved: compare under both readings and only assert what holds
        # under each.
        if units_per_pack is None:
            return []
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

    for item_id in unmatched_rx:
        rx_line = rx_by_id[item_id]
        findings.append(
            _finding(
                "RX_NOT_BILLED", "critical",
                f"Prescribed item {rx_line.drug_name or rx_line.raw_text!r} has no "
                "corresponding line on the bill.",
                prescribed_ref=item_id,
                detail={"drug_name": rx_line.drug_name, "raw_text": rx_line.raw_text},
            )
        )

    for item_id in unmatched_bill:
        bill_line = bill_by_id[item_id]
        drug = bill_drugs[item_id]
        non_medicine = _is_non_medicine(bill_line, drug)
        findings.append(
            _finding(
                "BILL_NOT_PRESCRIBED",
                "info" if non_medicine else "critical",
                f"Billed line {bill_line.drug_name or bill_line.raw_text!r} has no "
                "corresponding line on the prescription."
                + (" Recorded as a non-medicine line." if non_medicine else ""),
                billed_ref=item_id,
                detail={
                    "drug_name": bill_line.drug_name,
                    "raw_text": bill_line.raw_text,
                    "form": bill_line.form,
                    "non_medicine": non_medicine,
                    "line_total": (
                        str(bill_line.line_total) if bill_line.line_total is not None else None
                    ),
                },
            )
        )

        entry = entry_for(drug)
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
) -> ReconciliationResult:
    """Reconcile a prescription against a bill. Pure, deterministic, no LLM.

    Args:
        prescription: extracted prescription.
        bill: extracted pharmacy bill.
        processing_ms: override the measured wall time, for reproducible tests.

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
    findings.extend(_document_rules(prescription, bill, bill_by_id))
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
        "reconciled: verdict=%s score=%s pairs=%d findings=%d",
        verdict, score, len(pairs), len(findings),
    )
    return ReconciliationResult(
        verdict=verdict,
        score=score,
        findings=findings,
        matched_pairs=pairs,
        unmatched_prescribed=unmatched_rx,
        unmatched_billed=unmatched_bill,
        prescription=prescription,
        bill=bill,
        processing_ms=elapsed,
    )
