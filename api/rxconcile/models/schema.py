"""Data contracts for extraction and reconciliation.

**Identity.** Every :class:`PrescribedItem` and :class:`BilledItem` carries a
stable ``item_id``, assigned in document order at extraction time and unique
within its parent document. Every cross-reference -- findings, matched pairs,
unmatched lists -- keys on that identifier and never on ``raw_text``.

``raw_text`` is display data only. Two lines on one prescription can legitimately
be byte-identical (the same drug written twice with different durations is
routine), and bills repeat strings across lines constantly. Keying on
``raw_text`` would make those pairs ambiguous and would break the frontend's
ability to map a finding back to a specific row.

Uniqueness is enforced by validators, and referential integrity is enforced when
a :class:`ReconciliationResult` is constructed: a dangling reference fails loudly
rather than silently rendering as a blank row.

Nullability throughout follows the project rule that an illegible field is
emitted as ``None`` with a confidence score, never guessed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["critical", "warning", "info"]
"""Severity of a :class:`Finding`. Fixed vocabulary; callers may branch on it."""

Verdict = Literal["match", "match_with_warnings", "mismatch", "inconclusive"]
"""Overall outcome. ``inconclusive`` means the documents were too illegible to judge."""


class _Base(BaseModel):
    """Shared config: reject unknown keys, forbid mutation after construction.

    ``extra="forbid"`` makes schema drift a loud failure -- an extractor that
    invents a field, or a fixture with a typo, fails at parse time instead of
    silently dropping data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


def _duplicates(values: Sequence[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


class PrescribedItem(_Base):
    """One prescribed line, as read off the script."""

    item_id: str = Field(
        min_length=1,
        description="Stable ID, e.g. 'rx-01'. Document order, unique within the prescription.",
    )
    raw_text: str = Field(
        description="The line exactly as written. Display only; never used as a key."
    )
    drug_name: str | None = Field(default=None, description="Brand or generic name as written.")
    salt: str | None = Field(default=None, description="Active ingredient, if stated.")
    strength_value: float | None = Field(default=None, description="Numeric strength.")
    strength_unit: str | None = Field(
        default=None, description="Unit as written, e.g. mg, mcg, ml, IU, %."
    )
    form: str | None = Field(
        default=None,
        description="tablet, capsule, syrup, injection, ointment, drops, etc.",
    )
    dose_per_administration: float | None = Field(
        default=None, description="Units taken per administration."
    )
    frequency_raw: str | None = Field(
        default=None,
        description="Frequency exactly as written, e.g. '1-0-1', 'BD', 'TDS', 'SOS', 'HS'.",
    )
    duration_days: int | None = Field(default=None, ge=0, description="Course length in days.")
    route: str | None = Field(default=None, description="oral, topical, IV, etc.")
    instructions: str | None = Field(default=None, description="Free-text directions.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Legibility confidence for THIS item, 0-1."
    )


class BilledItem(_Base):
    """One line on the pharmacy bill."""

    item_id: str = Field(
        min_length=1,
        description="Stable ID, e.g. 'bill-01'. Document order, unique within the bill.",
    )
    raw_text: str = Field(
        description="The line exactly as printed. Display only; never used as a key."
    )
    drug_name: str | None = Field(default=None)
    salt: str | None = Field(default=None)
    strength_value: float | None = Field(default=None)
    strength_unit: str | None = Field(default=None)
    form: str | None = Field(default=None)
    quantity: float | None = Field(default=None, ge=0, description="Units dispensed.")
    pack_size: str | None = Field(
        default=None,
        description="Pack description exactly as printed, e.g. \"10'S\", '1x10'. Not parsed here.",
    )
    unit_price: Decimal | None = Field(default=None, description="Price per unit.")
    line_total: Decimal | None = Field(default=None, description="Line amount charged.")
    batch_no: str | None = Field(default=None)
    hsn_code: str | None = Field(default=None)
    confidence: float = Field(
        ge=0.0, le=1.0, description="Legibility confidence for THIS item, 0-1."
    )


class Prescription(_Base):
    """A prescription document and everything extracted from it."""

    patient_name: str | None = Field(default=None)
    patient_age: str | None = Field(
        default=None,
        description="Age exactly as written, e.g. '45', '45Y', '6 months'. Kept as text so "
        "an unstated unit is never invented.",
    )
    patient_sex: str | None = Field(default=None)
    prescriber_name: str | None = Field(default=None)
    prescriber_reg_no: str | None = Field(default=None)
    clinic_name: str | None = Field(default=None)
    date_issued: date | None = Field(
        default=None,
        description="ISO date. An ambiguous handwritten date must be null, not guessed.",
    )
    diagnosis_text: str | None = Field(default=None)
    items: list[PrescribedItem] = Field(default_factory=list)
    overall_legibility: float = Field(
        ge=0.0, le=1.0, description="Whole-document legibility, 0-1."
    )
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _item_ids_unique(self) -> Self:
        duplicated = _duplicates([item.item_id for item in self.items])
        if duplicated:
            raise ValueError(
                f"duplicate PrescribedItem.item_id within one prescription: {duplicated}. "
                "item_id must be unique within its parent document; cross-references key "
                "on it, so a duplicate makes findings ambiguous."
            )
        return self

    @property
    def item_ids(self) -> frozenset[str]:
        return frozenset(item.item_id for item in self.items)


class PharmacyBill(_Base):
    """A pharmacy bill and everything extracted from it."""

    pharmacy_name: str | None = Field(default=None)
    pharmacy_licence_no: str | None = Field(default=None)
    bill_no: str | None = Field(default=None)
    bill_date: date | None = Field(
        default=None, description="ISO date. An ambiguous date must be null, not guessed."
    )
    patient_name: str | None = Field(default=None)
    items: list[BilledItem] = Field(default_factory=list)
    subtotal: Decimal | None = Field(default=None)
    tax_total: Decimal | None = Field(default=None)
    grand_total: Decimal | None = Field(default=None)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _item_ids_unique(self) -> Self:
        duplicated = _duplicates([item.item_id for item in self.items])
        if duplicated:
            raise ValueError(
                f"duplicate BilledItem.item_id within one bill: {duplicated}. "
                "item_id must be unique within its parent document; cross-references key "
                "on it, so a duplicate makes findings ambiguous."
            )
        return self

    @property
    def item_ids(self) -> frozenset[str]:
        return frozenset(item.item_id for item in self.items)


class Finding(_Base):
    """One machine-readable discrepancy.

    Findings describe what the documents say and where they differ. They never
    contain clinical judgement, dosing advice, or a recommendation.
    """

    rule_code: str = Field(
        min_length=1,
        description="Stable identifier a caller can branch on, e.g. 'STRENGTH_MISMATCH'.",
    )
    severity: Severity
    message: str = Field(description="Plain English. Document discrepancy only, no advice.")
    prescribed_ref: str | None = Field(
        default=None, description="PrescribedItem.item_id. Never raw_text."
    )
    billed_ref: str | None = Field(
        default=None, description="BilledItem.item_id. Never raw_text."
    )
    detail: dict[str, Any] = Field(
        default_factory=dict, description="Structured evidence, e.g. expected vs found."
    )


class MatchedPair(_Base):
    """A prescribed item paired to a billed item.

    Named rather than a bare ``tuple[str, str]``: a tuple gives no indication
    which element is which, serialises to a positional JSON array, and leaves
    nowhere to put the pairing score.
    """

    prescribed_id: str = Field(min_length=1, description="PrescribedItem.item_id.")
    billed_id: str = Field(min_length=1, description="BilledItem.item_id.")
    similarity: float = Field(ge=0.0, le=1.0, description="Composite pairing score, 0-1.")


class ReconciliationResult(_Base):
    """The complete outcome of reconciling one prescription against one bill."""

    verdict: Verdict
    score: float = Field(ge=0.0, le=100.0, description="Overall agreement score, 0-100.")
    findings: list[Finding] = Field(default_factory=list)
    matched_pairs: list[MatchedPair] = Field(default_factory=list)
    unmatched_prescribed: list[str] = Field(
        default_factory=list, description="PrescribedItem.item_id values with no billed match."
    )
    unmatched_billed: list[str] = Field(
        default_factory=list, description="BilledItem.item_id values with no prescribed match."
    )
    prescription: Prescription
    bill: PharmacyBill
    processing_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        """Every referenced item_id must exist, and nothing may be both matched and unmatched.

        All problems are collected and reported together, so a caller fixing
        references sees the full picture rather than one error per round trip.
        """
        rx_ids = self.prescription.item_ids
        bill_ids = self.bill.item_ids
        problems: list[str] = []

        for index, finding in enumerate(self.findings):
            ref = finding.prescribed_ref
            if ref is not None and ref not in rx_ids:
                problems.append(
                    f"findings[{index}].prescribed_ref={ref!r} ({finding.rule_code}) "
                    f"is not a PrescribedItem.item_id"
                )
            ref = finding.billed_ref
            if ref is not None and ref not in bill_ids:
                problems.append(
                    f"findings[{index}].billed_ref={ref!r} ({finding.rule_code}) "
                    f"is not a BilledItem.item_id"
                )

        for index, pair in enumerate(self.matched_pairs):
            if pair.prescribed_id not in rx_ids:
                problems.append(
                    f"matched_pairs[{index}].prescribed_id={pair.prescribed_id!r} "
                    f"is not a PrescribedItem.item_id"
                )
            if pair.billed_id not in bill_ids:
                problems.append(
                    f"matched_pairs[{index}].billed_id={pair.billed_id!r} "
                    f"is not a BilledItem.item_id"
                )

        for index, item_id in enumerate(self.unmatched_prescribed):
            if item_id not in rx_ids:
                problems.append(
                    f"unmatched_prescribed[{index}]={item_id!r} is not a PrescribedItem.item_id"
                )
        for index, item_id in enumerate(self.unmatched_billed):
            if item_id not in bill_ids:
                problems.append(
                    f"unmatched_billed[{index}]={item_id!r} is not a BilledItem.item_id"
                )

        matched_rx = {pair.prescribed_id for pair in self.matched_pairs}
        matched_bill = {pair.billed_id for pair in self.matched_pairs}
        both_rx = sorted(matched_rx & set(self.unmatched_prescribed))
        both_bill = sorted(matched_bill & set(self.unmatched_billed))
        if both_rx:
            problems.append(
                f"prescribed item_id(s) {both_rx} appear in both a matched pair and "
                f"unmatched_prescribed"
            )
        if both_bill:
            problems.append(
                f"billed item_id(s) {both_bill} appear in both a matched pair and "
                f"unmatched_billed"
            )

        if problems:
            raise ValueError(
                "ReconciliationResult failed referential integrity:\n  - "
                + "\n  - ".join(problems)
            )
        return self
