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
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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


def valid_bbox(box: Sequence[float] | None) -> bool:
    """A box is usable only if it is inside the image and not inverted."""
    if box is None:
        return False
    if len(box) != 4:
        return False
    x0, y0, x1, y1 = box
    if not all(0.0 <= value <= 1.0 for value in box):
        return False
    return x1 > x0 and y1 > y0


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
    duration_raw: str | None = Field(
        default=None,
        description="Course length EXACTLY as written, e.g. 'x 5 days', '5/7', '৪ মাস'. "
        "Display and parsing source; never converted by the extractor.",
    )
    duration_days: int | None = Field(
        default=None,
        ge=0,
        description="Course length in days. Populated by the normalization layer from "
        "duration_raw, not by the extractor. Null whenever converting duration_raw "
        "would require an assumption the page does not state.",
    )
    route: str | None = Field(default=None, description="oral, topical, IV, etc.")
    instructions: str | None = Field(default=None, description="Free-text directions.")
    bbox: tuple[float, float, float, float] | None = Field(
        default=None,
        description="Where this line sits on the preprocessed image, as "
        "[x0, y0, x1, y1] normalised to 0-1. **Null when the model could not "
        "locate the line** -- never guessed, like every other field. Resolved "
        "across runs by IoU rather than exact equality; a box that moves between "
        "runs is not a location.",
    )
    agreement: dict[str, float] | None = Field(
        default=None,
        description="Per-field agreement ratio across the N extraction runs, e.g. "
        "{'drug_name': 1.0, 'strength_value': 0.67}. **This is the reliability "
        "signal.** None when N=1: a single run has no agreement, and reporting "
        "1.0 would be a lie.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="The MODEL'S OWN legibility score, retained for the record only. "
        "Measured uninformative (0.75-0.95 across 56 observations, sometimes "
        "inverted against reproducibility). **Nothing may gate on this.** Use "
        "`agreement` instead.",
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
    units_basis: Literal["pack", "unit"] | None = Field(
        default=None,
        description="What `quantity` counts: whole packs or individual dosage units. "
        "Set ONLY when the bill states it explicitly; null otherwise. Never inferred "
        "- misreading packs as units inflates a quantity comparison by the pack size.",
    )
    unit_price: Decimal | None = Field(default=None, description="Price per unit.")
    discount: Decimal | None = Field(
        default=None,
        description="Line discount AS PRINTED, in currency. Null when the bill prints "
        "no discount column -- which is NOT the same as a discount of zero, and the "
        "arithmetic check treats the two differently.",
    )
    line_total: Decimal | None = Field(default=None, description="Line amount charged.")
    batch_no: str | None = Field(default=None)
    expiry: date | None = Field(
        default=None,
        description="The LAST DAY this line is valid. A bill prints a month and year, "
        "and '07/2026' means good through 31 July 2026, so the month is stored as its "
        "final day. Null when nothing was printed or the form was unrecognised.",
    )
    hsn_code: str | None = Field(default=None)
    bbox: tuple[float, float, float, float] | None = Field(
        default=None,
        description="Where this line sits on the preprocessed image, as "
        "[x0, y0, x1, y1] normalised to 0-1. **Null when the model could not "
        "locate the line** -- never guessed, like every other field. Resolved "
        "across runs by IoU rather than exact equality; a box that moves between "
        "runs is not a location.",
    )
    agreement: dict[str, float] | None = Field(
        default=None,
        description="Per-field agreement ratio across the N extraction runs, e.g. "
        "{'drug_name': 1.0, 'strength_value': 0.67}. **This is the reliability "
        "signal.** None when N=1: a single run has no agreement, and reporting "
        "1.0 would be a lie.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="The MODEL'S OWN legibility score, retained for the record only. "
        "**Nothing may gate on this.** Use `agreement` instead.",
    )


class PrescribedTest(_Base):
    """One investigation ordered on a prescription.

    Lab work is ordered in the "Adv:" or "Investigations:" section and is often
    written as a panel name rather than as individual analytes.
    """

    item_id: str = Field(
        min_length=1,
        description="Stable ID, e.g. 'test-01'. Document order, assigned in Python.",
    )
    raw_text: str = Field(description="The line exactly as written. Display only.")
    test_name: str | None = Field(
        default=None, description="Test or panel name as written, else null."
    )
    panel: str | None = Field(
        default=None, description="Panel this belongs to, if the page names one."
    )
    urgency: str | None = Field(
        default=None, description="STAT, routine, fasting, as written. Else null."
    )
    bbox: tuple[float, float, float, float] | None = Field(
        default=None,
        description="Where this line sits on the image, normalised 0-1. Null when "
        "the model could not locate it.",
    )
    agreement: dict[str, float] | None = Field(
        default=None,
        description="Per-field agreement across the N runs. **The reliability "
        "signal.** None when N=1.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="The MODEL'S OWN legibility score. **Nothing may gate on this.**",
    )


class BilledTest(_Base):
    """One lab line on a bill.

    Lab bills and pharmacy bills are frequently separate documents, so a bill may
    carry tests, medicines, or both.
    """

    item_id: str = Field(
        min_length=1, description="Stable ID, e.g. 'billtest-01'. Printed order."
    )
    raw_text: str = Field(description="The line exactly as printed. Display only.")
    test_name: str | None = Field(default=None)
    panel: str | None = Field(default=None)
    quantity: float | None = Field(default=None, ge=0)
    unit_price: Decimal | None = Field(default=None)
    line_total: Decimal | None = Field(default=None)
    bbox: tuple[float, float, float, float] | None = Field(default=None)
    agreement: dict[str, float] | None = Field(default=None)
    confidence: float = Field(
        ge=0.0, le=1.0, description="Model's own score. **Nothing may gate on this.**"
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
    date_order_assumed: bool = Field(
        default=False,
        description="True when the day/month order could not be read off the page and "
        "the configured convention decided it. **An assumed date is not a read one**, "
        "and the engine raises DATE_ORDER_ASSUMED rather than letting it pass as fact.",
    )
    diagnosis_text: str | None = Field(default=None)
    items: list[PrescribedItem] = Field(default_factory=list)
    tests: list[PrescribedTest] = Field(
        default_factory=list, description="Investigations ordered, in document order."
    )
    investigations_present: bool | None = Field(
        default=None,
        description="Whether the page carries an investigations section at all. "
        "**Absent and unreadable are different results**: an empty `tests` list with "
        "this True means the section exists but could not be read, which is not the "
        "same as no tests being ordered. Null when the model could not tell.",
    )
    overall_legibility: float = Field(
        ge=0.0,
        le=1.0,
        description="The MODEL'S OWN whole-document legibility score. Retained for "
        "the record; **nothing may gate on this** (measured 0.75-0.95, never "
        "below the 0.4 floor even on unreproducible documents).",
    )
    run_item_counts: list[int] = Field(
        default_factory=list,
        description="Item count returned by each extraction run. Differing counts "
        "mean a line appeared in some runs and not others, which the engine "
        "raises as ITEM_COUNT_UNSTABLE.",
    )
    unstable_lines: list[str] = Field(
        default_factory=list,
        description="raw_text of lines present in some runs but not all.",
    )
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _item_ids_unique(self) -> Self:
        duplicated = _duplicates(
            [item.item_id for item in self.items] + [test.item_id for test in self.tests]
        )
        if duplicated:
            raise ValueError(
                f"duplicate item_id within one prescription: {duplicated}. "
                "item_id must be unique within its parent document; cross-references key "
                "on it, so a duplicate makes findings ambiguous."
            )
        return self

    @property
    def item_ids(self) -> frozenset[str]:
        """Every referenceable id on this document, medicines and tests alike."""
        return frozenset(
            [item.item_id for item in self.items] + [test.item_id for test in self.tests]
        )

    @property
    def test_ids(self) -> frozenset[str]:
        return frozenset(test.item_id for test in self.tests)


class PharmacyBill(_Base):
    """A pharmacy bill and everything extracted from it."""

    pharmacy_name: str | None = Field(default=None)
    pharmacy_licence_no: str | None = Field(default=None)
    gstin: str | None = Field(
        default=None, description="GSTIN exactly as printed. Never normalised here."
    )
    pharmacy_address: str | None = Field(
        default=None,
        description="Address block as printed. Used only to compare the state against "
        "the GSTIN's state code, which is informational -- a chain may bill from "
        "another state entirely.",
    )
    bill_no: str | None = Field(default=None)
    bill_date: date | None = Field(
        default=None, description="ISO date. An ambiguous date must be null, not guessed."
    )
    date_order_assumed: bool = Field(
        default=False,
        description="True when the day/month order was decided by convention rather "
        "than read off the page.",
    )
    patient_name: str | None = Field(default=None)
    items: list[BilledItem] = Field(default_factory=list)
    tests: list[BilledTest] = Field(
        default_factory=list,
        description="Lab lines on this bill. Lab and pharmacy bills are often "
        "separate documents, so this may be populated while items is empty.",
    )
    subtotal: Decimal | None = Field(default=None)
    discount_total: Decimal | None = Field(
        default=None, description="Bill-level discount as printed, in currency."
    )
    tax_total: Decimal | None = Field(default=None)
    grand_total: Decimal | None = Field(default=None)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    run_item_counts: list[int] = Field(
        default_factory=list,
        description="Item count returned by each extraction run. Differing counts "
        "mean a line appeared in some runs and not others, which the engine "
        "raises as ITEM_COUNT_UNSTABLE.",
    )
    unstable_lines: list[str] = Field(
        default_factory=list,
        description="raw_text of lines present in some runs but not all.",
    )
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _item_ids_unique(self) -> Self:
        duplicated = _duplicates(
            [item.item_id for item in self.items] + [test.item_id for test in self.tests]
        )
        if duplicated:
            raise ValueError(
                f"duplicate item_id within one bill: {duplicated}. "
                "item_id must be unique within its parent document; cross-references key "
                "on it, so a duplicate makes findings ambiguous."
            )
        return self

    @property
    def item_ids(self) -> frozenset[str]:
        """Every referenceable id on this document, medicines and tests alike."""
        return frozenset(
            [item.item_id for item in self.items] + [test.item_id for test in self.tests]
        )

    @property
    def test_ids(self) -> frozenset[str]:
        return frozenset(test.item_id for test in self.tests)


class CanonicalMatch(_Base):
    """What the dictionary matcher resolved one line to.

    **Derived, not transcribed.** ``PrescribedItem.salt`` is what the model read
    off the page and is usually null, because prescriptions print brand names,
    not compositions. This is what ``normalize.matcher`` resolved that brand to,
    and it is deliberately a separate object: conflating a value read off a
    document with a value looked up in a dictionary is exactly the confusion the
    identity rule exists to prevent.

    The engine computed this on every run from the beginning; it simply had no
    way to report it, so a salt reached the client only as a side effect of a
    BRAND_SUBSTITUTION or SCHEDULE_H_UNBACKED finding happening to fire.
    """

    item_id: str = Field(min_length=1, description="The line this resolves.")
    side: Literal["prescription", "bill"]
    name: str | None = Field(
        default=None,
        description="Canonical name: the brand for a brand match, the ingredient "
        "for a salt match, null when unresolved.",
    )
    salt: str | None = Field(default=None, description="Composition, null when unresolved.")
    match_score: float = Field(default=0.0, ge=0.0, le=100.0)
    method: str = Field(default="unresolved")

    @property
    def resolved(self) -> bool:
        return self.method != "unresolved"


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


class ReviewSummary(_Base):
    """Headline counts, so a caller can lead with numbers rather than a wall of findings.

    Derived from the documents rather than supplied by the engine: these are
    pure counts over data already present, and computing them here means they
    cannot drift from the findings or be forgotten.

    An item whose ``agreement`` is None (a single-run extraction) is not counted
    as needing review -- there is no evidence either way, and counting it would
    overstate what was measured.
    """

    agreement_measured: bool = Field(
        default=False,
        description="Whether agreement was measurable at all. False for a single-run "
        "extraction, where the counts below are null rather than zero.",
    )
    items_needing_review: int | None = Field(
        default=None,
        ge=0,
        description="Items with at least one field below full agreement across runs. "
        "**None when agreement was not measured** -- a single run cannot show that "
        "nothing needs review, and reporting 0 would read as a clean result.",
    )
    fields_nulled_by_disagreement: int | None = Field(
        default=None,
        ge=0,
        description="Fields resolved to null because the runs did not agree. None when "
        "agreement was not measured.",
    )
    unstable_line_count: int | None = Field(
        default=None,
        ge=0,
        description="Lines present in some extraction runs but not all, across both "
        "documents. None when a single run made instability undetectable.",
    )
    checks_unavailable: int = Field(
        default=0,
        ge=0,
        description="Rules that could not run because an input was absent. "
        "**'We checked and found nothing' and 'we could not check' are different "
        "results**, and this is what keeps them distinguishable.",
    )

    @property
    def needs_attention(self) -> bool:
        """True when something measured warrants review, or a check could not run."""
        if self.checks_unavailable:
            return True
        if not self.agreement_measured:
            return False
        return bool(
            self.items_needing_review
            or self.fields_nulled_by_disagreement
            or self.unstable_line_count
        )


def _summarise_items(items: Sequence[PrescribedItem | BilledItem]) -> tuple[int, int]:
    """(items needing review, fields nulled by disagreement) for one document."""
    needing = 0
    nulled = 0
    for item in items:
        agreement = item.agreement
        if not agreement:
            continue
        if min(agreement.values()) < 1.0:
            needing += 1
        for field, ratio in agreement.items():
            if ratio < 1.0 and getattr(item, field, None) is None:
                nulled += 1
    return needing, nulled


#: Rule code emitted when a check could not run for want of an input.
CHECK_UNAVAILABLE_CODE: Final[str] = "CHECK_UNAVAILABLE"


def build_review_summary(
    prescription: Prescription,
    bill: PharmacyBill,
    findings: Sequence[Finding] = (),
) -> ReviewSummary:
    """Derive the headline counts from both documents.

    When no item carries agreement data -- a single-run extraction -- every count
    is None rather than 0. Zero would claim that nothing needs review, which a
    single run cannot establish.
    """
    unavailable = sum(
        1 for finding in findings if finding.rule_code == CHECK_UNAVAILABLE_CODE
    )
    measured = (
        any(item.agreement for item in prescription.items)
        or any(item.agreement for item in bill.items)
        or len(prescription.run_item_counts) > 1
        or len(bill.run_item_counts) > 1
    )
    if not measured:
        return ReviewSummary(agreement_measured=False, checks_unavailable=unavailable)

    rx_needing, rx_nulled = _summarise_items(prescription.items)
    bill_needing, bill_nulled = _summarise_items(bill.items)
    return ReviewSummary(
        agreement_measured=True,
        items_needing_review=rx_needing + bill_needing,
        fields_nulled_by_disagreement=rx_nulled + bill_nulled,
        unstable_line_count=len(prescription.unstable_lines) + len(bill.unstable_lines),
        checks_unavailable=unavailable,
    )


ReimbursementCategory = Literal["eligible", "not_eligible", "needs_review", "non_medicine"]


class ReimbursementLine(_Base):
    """One billed line and the bucket it fell into."""

    item_id: str = Field(min_length=1, description="BilledItem or BilledTest item_id.")
    description: str = Field(description="Drug or test name, else the raw line.")
    amount: Decimal | None = Field(
        default=None,
        description="line_total as printed. **Null when the bill prints no amount**, "
        "and then excluded from every total rather than counted as zero.",
    )
    category: ReimbursementCategory
    reason: str = Field(description="Why this line landed where it did, in plain words.")
    rule_codes: list[str] = Field(default_factory=list)


class ReimbursementSummary(_Base):
    """Which billed items are supported by the prescription, and for how much.

    **Not an insurance determination.** Copay tiers, coverage rules and policy
    limits appear in neither document, are not modelled, and are not inferred.
    This is an assessment of which billed lines have a prescription behind them
    -- nothing more. Nothing here approves, settles or rejects anything.
    """

    eligible_total: Decimal = Decimal("0")
    eligible_line_count: int = 0
    not_eligible_total: Decimal = Decimal("0")
    not_eligible_line_count: int = 0
    needs_review_total: Decimal = Decimal("0")
    needs_review_line_count: int = 0
    non_medicine_total: Decimal = Field(
        default=Decimal("0"),
        description="Billed lines that are not medicines. Reported separately rather "
        "than as unprescribed: a delivery charge is out of scope, not an accusation.",
    )
    non_medicine_line_count: int = 0
    lines_without_amount: int = Field(
        default=0,
        description="Billed lines with no printed amount. Excluded from the totals "
        "above, and reported so a total is never quietly incomplete.",
    )
    currency: str = "INR"
    lines: list[ReimbursementLine] = Field(default_factory=list)

    @property
    def assessed_total(self) -> Decimal:
        return (
            self.eligible_total
            + self.not_eligible_total
            + self.needs_review_total
            + self.non_medicine_total
        )


#: The conditions the Verify screen offers. "Other" reveals a free-text field.
CONDITIONS: Final[tuple[str, ...]] = (
    "Fever / infection",
    "Diabetes",
    "Hypertension",
    "Respiratory",
    "Gastric",
    "Dental",
    "Injury",
    "Skin",
    "Other",
)


class Submission(_Base):
    """What the operator told us they were uploading.

    The engine used to INFER whether a lab bill existed, by looking at whether
    the extracted bill carried lab lines. That produced "no lab bill supplied"
    against a bill holding five of them. The operator now says which document is
    which, so completeness is read from this rather than guessed from content.

    An absent lab report or lab bill is a legitimate choice, not a gap. Only a
    prescription that ordered tests with no lab bill behind it is a missing
    document.
    """

    condition: str | None = Field(
        default=None, description="Selected condition, or the free text behind 'Other'."
    )
    description: str | None = Field(default=None, description="Operator's notes. Optional.")
    prescription_supplied: bool = True
    pharmacy_bill_supplied: bool = True
    lab_report_supplied: bool = False
    lab_bill_supplied: bool = False


class ReconciliationResult(_Base):
    """The complete outcome of reconciling one prescription against one bill."""

    verdict: Verdict
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Overall agreement score 0-100, or None when the verdict is "
        "'inconclusive'. A number implies a measurement was possible; emitting 0 "
        "would read as 'measured, terrible' rather than 'not measurable'.",
    )
    findings: list[Finding] = Field(default_factory=list)
    matched_pairs: list[MatchedPair] = Field(default_factory=list)
    unmatched_prescribed: list[str] = Field(
        default_factory=list, description="PrescribedItem.item_id values with no billed match."
    )
    unmatched_billed: list[str] = Field(
        default_factory=list, description="BilledItem.item_id values with no prescribed match."
    )
    submission: Submission = Field(
        default_factory=Submission,
        description="What was uploaded, as stated by the operator. Document-completeness "
        "checks read this rather than inferring from extracted content.",
    )
    reimbursement: ReimbursementSummary = Field(
        default_factory=ReimbursementSummary,
        description="Which billed lines the prescription supports. Not an insurance "
        "determination; see ReimbursementSummary.",
    )
    canonical: list[CanonicalMatch] = Field(
        default_factory=list,
        description="The dictionary match behind every medicine line, both sides. "
        "Unresolved lines appear with null name and salt rather than being omitted, "
        "so a caller can tell 'looked up, no match' from 'never looked up'.",
    )
    matched_tests: list[MatchedPair] = Field(
        default_factory=list,
        description="Ordered tests paired to billed lab lines, by the same shapes the "
        "medicine side uses.",
    )
    unmatched_prescribed_tests: list[str] = Field(default_factory=list)
    unmatched_billed_tests: list[str] = Field(default_factory=list)
    prescription: Prescription
    bill: PharmacyBill
    processing_ms: int = Field(ge=0)
    review_summary: ReviewSummary = Field(
        default_factory=ReviewSummary,
        description="Headline counts for the UI. Always recomputed from the "
        "documents during validation, so a supplied value cannot drift.",
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_review_summary(cls, data: Any) -> Any:  # noqa: ANN401
        # Any is pydantic's contract for a mode='before' validator: the input
        # is whatever the caller passed, before any coercion has happened.
        """Recompute review_summary from the documents, overriding any input.

        Done in ``before`` rather than ``after`` because the model is frozen.
        Overriding rather than validating a supplied value keeps the count
        honest without making every caller compute it.
        """
        if not isinstance(data, dict):
            return data
        prescription = data.get("prescription")
        bill = data.get("bill")
        if prescription is None or bill is None:
            return data
        try:
            rx = (
                prescription
                if isinstance(prescription, Prescription)
                else Prescription.model_validate(prescription)
            )
            ph = bill if isinstance(bill, PharmacyBill) else PharmacyBill.model_validate(bill)
        except ValidationError:
            # Let the normal field validation report the real problem.
            return data
        raw_findings = data.get("findings") or []
        findings: list[Finding] = []
        for entry in raw_findings:
            if isinstance(entry, Finding):
                findings.append(entry)
            else:
                try:
                    findings.append(Finding.model_validate(entry))
                except ValidationError:
                    return data
        return {**data, "review_summary": build_review_summary(rx, ph, findings)}

    @model_validator(mode="after")
    def _score_matches_verdict(self) -> Self:
        """score is None if and only if the verdict is 'inconclusive'."""
        if self.verdict == "inconclusive" and self.score is not None:
            raise ValueError(
                f"verdict is 'inconclusive' but score is {self.score}. An "
                "inconclusive result measured nothing reliably, so it must carry "
                "no score."
            )
        if self.verdict != "inconclusive" and self.score is None:
            raise ValueError(
                f"verdict is {self.verdict!r} but score is None. Only an "
                "'inconclusive' verdict may omit the score."
            )
        return self

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

        for index, line in enumerate(self.reimbursement.lines):
            if line.item_id not in bill_ids:
                problems.append(
                    f"reimbursement.lines[{index}].item_id={line.item_id!r} is not a "
                    f"billed item_id"
                )

        for index, match in enumerate(self.canonical):
            pool = rx_ids if match.side == "prescription" else bill_ids
            if match.item_id not in pool:
                problems.append(
                    f"canonical[{index}].item_id={match.item_id!r} is not an item_id "
                    f"on the {match.side}"
                )

        for index, pair in enumerate(self.matched_tests):
            if pair.prescribed_id not in rx_ids:
                problems.append(
                    f"matched_tests[{index}].prescribed_id={pair.prescribed_id!r} "
                    f"is not a PrescribedTest.item_id"
                )
            if pair.billed_id not in bill_ids:
                problems.append(
                    f"matched_tests[{index}].billed_id={pair.billed_id!r} "
                    f"is not a BilledTest.item_id"
                )

        for index, item_id in enumerate(self.unmatched_prescribed_tests):
            if item_id not in rx_ids:
                problems.append(
                    f"unmatched_prescribed_tests[{index}]={item_id!r} is not a "
                    f"PrescribedTest.item_id"
                )
        for index, item_id in enumerate(self.unmatched_billed_tests):
            if item_id not in bill_ids:
                problems.append(
                    f"unmatched_billed_tests[{index}]={item_id!r} is not a "
                    f"BilledTest.item_id"
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
