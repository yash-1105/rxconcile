"""Which billed lines the prescription supports, and for how much.

The load-bearing tests here are the ones about money that cannot be added and
about what this deliberately does NOT do.
"""

from __future__ import annotations

from decimal import Decimal

from rxconcile.models import (
    BilledItem,
    BilledTest,
    PharmacyBill,
    PrescribedItem,
    PrescribedTest,
    Prescription,
    ReconciliationResult,
)
from rxconcile.reconcile import engine


def rx(*names: str) -> Prescription:
    return Prescription(
        overall_legibility=0.95,
        items=[
            PrescribedItem(
                item_id=f"rx-{i:02d}", raw_text=name, drug_name=name,
                strength_value=650.0, strength_unit="mg", form="tablet",
                # Fully dosed on purpose: without these the quantity check
                # cannot run, and a line whose check could not run is
                # needs_review by design rather than eligible.
                dose_per_administration=1.0, frequency_raw="1-0-1", duration_days=5,
                confidence=0.9,
            )
            for i, name in enumerate(names, start=1)
        ],
    )


def bill(*rows: tuple[str, str | None]) -> PharmacyBill:
    return PharmacyBill(
        currency="INR", pharmacy_licence_no="TN/2019/337821",
        items=[
            BilledItem(
                item_id=f"bill-{i:02d}", raw_text=name, drug_name=name,
                strength_value=650.0, strength_unit="mg", form="tablet",
                quantity=10.0, units_basis="unit", pack_size="10'S",
                line_total=Decimal(total) if total is not None else None,
                confidence=0.9,
            )
            for i, (name, total) in enumerate(rows, start=1)
        ],
    )


def run(p: Prescription, b: PharmacyBill) -> ReconciliationResult:
    return engine.reconcile(p, b, processing_ms=0)


def test_a_matched_line_with_nothing_against_it_is_eligible() -> None:
    result = run(rx("Dolo"), bill(("Dolo", "220.00")))
    money = result.reimbursement
    assert money.eligible_total == Decimal("220.00")
    assert money.eligible_line_count == 1
    assert money.not_eligible_total == Decimal("0")
    assert money.needs_review_total == Decimal("0")


def test_an_unprescribed_line_is_not_eligible() -> None:
    result = run(rx("Dolo"), bill(("Dolo", "220.00"), ("Zincovit", "180.00")))
    money = result.reimbursement
    assert money.not_eligible_total == Decimal("180.00")
    assert money.not_eligible_line_count == 1
    line = next(x for x in money.lines if x.category == "not_eligible")
    assert line.description == "Zincovit"
    assert "prescription" in line.reason.lower()


def test_a_schedule_h_line_with_nothing_behind_it_is_not_eligible() -> None:
    result = run(rx("Dolo"), bill(("Dolo", "220.00"), ("Alprax", "95.00")))
    money = result.reimbursement
    codes = {c for line in money.lines for c in line.rule_codes}
    assert "SCHEDULE_H_UNBACKED" in codes or "BILL_NOT_PRESCRIBED" in codes
    assert money.not_eligible_total == Decimal("95.00")


def test_a_discrepancy_on_the_matched_line_needs_review() -> None:
    prescription = rx("Telma")
    billed = PharmacyBill(
        currency="INR", pharmacy_licence_no="TN/2019/337821",
        items=[
            BilledItem(
                item_id="bill-01", raw_text="TELMA", drug_name="Telma",
                strength_value=80.0, strength_unit="mg", form="tablet",
                line_total=Decimal("310.00"), confidence=0.9,
            )
        ],
    )
    money = run(prescription, billed).reimbursement
    assert money.needs_review_total == Decimal("310.00")
    assert money.eligible_total == Decimal("0")


def test_every_billed_line_lands_in_exactly_one_bucket() -> None:
    result = run(rx("Dolo", "Telma"), bill(("Dolo", "220.00"), ("Zincovit", "180.00")))
    money = result.reimbursement
    assert len(money.lines) == len(result.bill.items) + len(result.bill.tests)
    assert len({line.item_id for line in money.lines}) == len(money.lines)
    counted = (
        money.eligible_line_count + money.not_eligible_line_count + money.needs_review_line_count
    )
    assert counted == len(money.lines)


def test_a_line_whose_check_could_not_run_needs_review_not_eligible() -> None:
    """The distinction the whole view rests on: unchecked is not approved."""
    prescription = Prescription(
        overall_legibility=0.95,
        items=[
            PrescribedItem(item_id="rx-01", raw_text="Dolo", drug_name="Dolo",
                           strength_value=650.0, strength_unit="mg", form="tablet",
                           confidence=0.9)
        ],
    )
    billed = PharmacyBill(
        currency="INR", pharmacy_licence_no="TN/2019/337821",
        items=[
            BilledItem(item_id="bill-01", raw_text="DOLO", drug_name="Dolo",
                       strength_value=650.0, strength_unit="mg", form="tablet",
                       quantity=10.0, pack_size="10'S", line_total=Decimal("220.00"),
                       confidence=0.9)
        ],
    )
    money = run(prescription, billed).reimbursement
    assert money.eligible_total == Decimal("0")
    assert money.needs_review_total == Decimal("220.00")
    line = money.lines[0]
    assert "could not be completed" in line.reason


def test_a_line_with_no_printed_amount_is_reported_not_counted_as_zero() -> None:
    """A total quietly missing a line is worse than one that says it is incomplete."""
    result = run(rx("Dolo", "Pan"), bill(("Dolo", "220.00"), ("Pan", None)))
    money = result.reimbursement
    assert money.lines_without_amount == 1
    assert money.eligible_total == Decimal("220.00")
    unpriced = next(line for line in money.lines if line.amount is None)
    assert unpriced.description == "Pan"


def test_totals_only_ever_sum_lines_in_their_own_bucket() -> None:
    result = run(rx("Dolo"), bill(("Dolo", "220.00"), ("Zincovit", "180.00")))
    money = result.reimbursement
    for category, total in (
        ("eligible", money.eligible_total),
        ("not_eligible", money.not_eligible_total),
        ("needs_review", money.needs_review_total),
    ):
        expected = sum(
            (line.amount for line in money.lines
             if line.category == category and line.amount is not None),
            Decimal("0"),
        )
        assert total == expected


def test_a_lab_line_is_assessed_too() -> None:
    """A diagnostic bill is a bill; excluding tests would report zero eligible."""
    prescription = Prescription(
        overall_legibility=0.95,
        investigations_present=True,
        tests=[PrescribedTest(item_id="test-01", raw_text="Adv: CBC", test_name="CBC",
                              confidence=0.9)],
    )
    billed = PharmacyBill(
        currency="INR", pharmacy_licence_no="TN/2019/337821",
        tests=[
            BilledTest(item_id="billtest-01", raw_text="CBC", test_name="CBC",
                       line_total=Decimal("450.00"), quantity=1.0, confidence=0.9),
            BilledTest(item_id="billtest-02", raw_text="Lipid Profile",
                       test_name="Lipid Profile", line_total=Decimal("800.00"),
                       quantity=1.0, confidence=0.9),
        ],
    )
    money = run(prescription, billed).reimbursement
    assert money.eligible_total == Decimal("450.00")
    assert money.not_eligible_total == Decimal("800.00")


def test_an_empty_bill_produces_zero_totals_and_no_lines() -> None:
    money = run(rx(), PharmacyBill(currency="INR")).reimbursement
    assert money.lines == []
    assert money.eligible_total == Decimal("0")
    assert money.lines_without_amount == 0


def test_nothing_here_claims_to_be_an_insurance_determination() -> None:
    """Copy discipline, pinned: no approving, claiming or settling."""
    from rxconcile.models.schema import ReimbursementSummary

    text = " ".join(
        filter(None, [ReimbursementSummary.__doc__, *(
            field.description for field in ReimbursementSummary.model_fields.values()
        )])
    ).lower()
    for forbidden in ("approved", "claim", "settlement"):
        assert forbidden not in text, f"{forbidden!r} must not appear in reimbursement copy"
    assert "not an insurance determination" in text
