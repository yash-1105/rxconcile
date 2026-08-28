"""Null-input invariants for the engine.

Three times a *correct* never-guess null produced wrong engine behaviour, each
found by accident. These are the properties that must hold for every combination
of absent fields, not just the ones someone happened to try.

See docs/NULL_MATRIX.md.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rxconcile.models import (
    BilledItem,
    BilledTest,
    Finding,
    PharmacyBill,
    PrescribedItem,
    PrescribedTest,
    Prescription,
    ReconciliationResult,
)
from rxconcile.models.schema import CHECK_UNAVAILABLE_CODE
from rxconcile.reconcile import engine

# Fields the extractor may legitimately return null for.
RX_NULLABLE = (
    "drug_name", "salt", "strength_value", "strength_unit", "form",
    "dose_per_administration", "frequency_raw", "duration_raw", "duration_days",
    "route", "instructions",
)
BILL_NULLABLE = (
    "drug_name", "salt", "strength_value", "strength_unit", "form", "quantity",
    "pack_size", "units_basis", "unit_price", "line_total", "batch_no", "hsn_code",
)
#: Nullable fields on an ordered test and a billed test line. raw_text is
#: absent from both lists on purpose: it is never nulled, on either side.
TEST_NULLABLE = ("test_name", "panel", "urgency")
BILLTEST_NULLABLE = ("test_name", "panel", "quantity", "unit_price", "line_total")
DOC_NULLABLE = ("patient_name", "date_issued", "investigations_present")
BILLDOC_NULLABLE = ("patient_name", "bill_date")

RX_FULL: dict[str, Any] = {
    "item_id": "rx-01", "raw_text": "Tab. Dolo 650 1-0-1 x 5 days", "drug_name": "Dolo",
    "salt": "Paracetamol", "strength_value": 650.0, "strength_unit": "mg",
    "form": "tablet", "dose_per_administration": 1.0, "frequency_raw": "1-0-1",
    "duration_raw": "x 5 days", "duration_days": 5, "route": "oral",
    "instructions": "after food", "confidence": 0.9, "agreement": {"drug_name": 1.0},
}
BILL_FULL: dict[str, Any] = {
    "item_id": "bill-01", "raw_text": "DOLO 650 TAB 10'S", "drug_name": "Dolo",
    "salt": "Paracetamol", "strength_value": 650.0, "strength_unit": "mg",
    "form": "tablet", "quantity": 10.0, "pack_size": "10'S", "units_basis": "unit",
    "unit_price": "2.20", "line_total": "22.00", "batch_no": "B1", "hsn_code": "3004",
    "confidence": 0.9, "agreement": {"drug_name": 1.0},
}


RX_TEST_FULL: dict[str, Any] = {
    "item_id": "test-01", "raw_text": "Adv: [?] (fasting)", "test_name": "SGPT",
    "panel": None, "urgency": "fasting", "confidence": 0.9,
    "agreement": {"test_name": 1.0},
}
BILL_TEST_FULL: dict[str, Any] = {
    "item_id": "billtest-01", "raw_text": "[?] .......... 250.00", "test_name": "SGPT",
    "panel": None, "quantity": 1.0, "unit_price": "250.00", "line_total": "250.00",
    "confidence": 0.9, "agreement": {"test_name": 1.0},
}


def build(
    rx_nulls: frozenset[str],
    bill_nulls: frozenset[str],
    doc_nulls: frozenset[str],
    billdoc_nulls: frozenset[str],
    test_nulls: frozenset[str] = frozenset(),
    billtest_nulls: frozenset[str] = frozenset(),
) -> ReconciliationResult:
    rx = {**RX_FULL, **dict.fromkeys(rx_nulls)}
    bl = {**BILL_FULL, **dict.fromkeys(bill_nulls)}
    rx_test = {**RX_TEST_FULL, **dict.fromkeys(test_nulls)}
    bill_test = {**BILL_TEST_FULL, **dict.fromkeys(billtest_nulls)}
    prescription = Prescription(
        patient_name=None if "patient_name" in doc_nulls else "A. Kulkarni",
        date_issued=None if "date_issued" in doc_nulls else dt.date(2026, 3, 14),
        overall_legibility=0.9,
        run_item_counts=[3, 3, 3],
        items=[PrescribedItem(**rx)],
        tests=[PrescribedTest(**rx_test)],
        investigations_present=None if "investigations_present" in doc_nulls else True,
    )
    bill = PharmacyBill(
        patient_name=None if "patient_name" in billdoc_nulls else "A. Kulkarni",
        bill_date=None if "bill_date" in billdoc_nulls else dt.date(2026, 3, 14),
        run_item_counts=[3, 3, 3],
        items=[BilledItem(**bl)],
        tests=[BilledTest(**bill_test)],
    )
    return engine.reconcile(prescription, bill, processing_ms=0)


subsets = st.frozensets(st.sampled_from(RX_NULLABLE))
bill_subsets = st.frozensets(st.sampled_from(BILL_NULLABLE))
doc_subsets = st.frozensets(st.sampled_from(DOC_NULLABLE))
billdoc_subsets = st.frozensets(st.sampled_from(BILLDOC_NULLABLE))
test_subsets = st.frozensets(st.sampled_from(TEST_NULLABLE))
billtest_subsets = st.frozensets(st.sampled_from(BILLTEST_NULLABLE))

PROPERTY_SETTINGS = settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def criticals(result: ReconciliationResult) -> list[Finding]:
    return [f for f in result.findings if f.severity == "critical"]


@PROPERTY_SETTINGS
@given(subsets, bill_subsets, doc_subsets, billdoc_subsets, test_subsets, billtest_subsets)
def test_engine_never_raises_on_any_combination_of_nulls(
    rx_nulls: frozenset[str],
    bill_nulls: frozenset[str],
    doc_nulls: frozenset[str],
    billdoc_nulls: frozenset[str],
    test_nulls: frozenset[str],
    billtest_nulls: frozenset[str],
) -> None:
    """Absent data must never crash the engine."""
    result = build(rx_nulls, bill_nulls, doc_nulls, billdoc_nulls, test_nulls, billtest_nulls)
    assert result.verdict in {"match", "match_with_warnings", "mismatch", "inconclusive"}


@PROPERTY_SETTINGS
@given(subsets, bill_subsets, doc_subsets, billdoc_subsets, test_subsets, billtest_subsets)
def test_absent_data_alone_never_produces_a_critical(
    rx_nulls: frozenset[str],
    bill_nulls: frozenset[str],
    doc_nulls: frozenset[str],
    billdoc_nulls: frozenset[str],
    test_nulls: frozenset[str],
    billtest_nulls: frozenset[str],
) -> None:
    """Nulling fields on an otherwise-matching pair must not manufacture a critical.

    The baseline pair reconciles cleanly. Every discrepancy a critical could
    describe is therefore absent, so any critical here is an assertion built on
    missing data -- which is how an illegible drug name became a confident
    'not dispensed'.
    """
    result = build(rx_nulls, bill_nulls, doc_nulls, billdoc_nulls, test_nulls, billtest_nulls)
    offenders = [f.rule_code for f in criticals(result)]
    assert not offenders, (
        f"critical finding(s) {offenders} from absent data alone; "
        f"rx_nulls={sorted(rx_nulls)} bill_nulls={sorted(bill_nulls)}"
    )


@PROPERTY_SETTINGS
@given(subsets, bill_subsets, doc_subsets, billdoc_subsets, test_subsets, billtest_subsets)
def test_a_skipped_check_is_always_recorded(
    rx_nulls: frozenset[str],
    bill_nulls: frozenset[str],
    doc_nulls: frozenset[str],
    billdoc_nulls: frozenset[str],
    test_nulls: frozenset[str],
    billtest_nulls: frozenset[str],
) -> None:
    """If an input a check needs is absent, the result must say the check did not run.

    This is the invariant the whole audit exists to protect: 'we checked and
    found nothing' and 'we could not check' must never be indistinguishable.
    """
    result = build(rx_nulls, bill_nulls, doc_nulls, billdoc_nulls, test_nulls, billtest_nulls)
    unavailable = {
        str(f.detail.get("check"))
        for f in result.findings
        if f.rule_code == CHECK_UNAVAILABLE_CODE
    }

    # Pair-level checks only apply when the lines paired at all. An unpaired line
    # is reported by the counterpart rules instead, which say plainly that it
    # could not be identified -- a strength-unavailable finding for a pair that
    # does not exist would be noise, not information.
    paired = bool(result.matched_pairs)
    if not paired:
        assert unavailable, "an unpaired line recorded nothing at all"

    expectations: list[tuple[bool, str]] = [
        (paired and ("strength_value" in rx_nulls or "strength_value" in bill_nulls),
         "strength"),
        (paired and ("form" in rx_nulls or "form" in bill_nulls), "dosage form"),
        (
            paired
            and bool(
                # The baseline frequency is positional, so a null dose does not
                # block the check -- the slots carry it.
                "frequency_raw" in rx_nulls
                or {"duration_raw", "duration_days"} <= rx_nulls
                or "quantity" in bill_nulls
                # pack_size only blocks the check when the basis is not declared;
                # units_basis="unit" makes the quantity directly usable.
                or ("pack_size" in bill_nulls and "units_basis" in bill_nulls)
            ),
            "quantity",
        ),
        (bool(doc_nulls & {"patient_name"}) or bool(billdoc_nulls & {"patient_name"}),
         "patient name"),
        (bool(doc_nulls & {"date_issued"}) or bool(billdoc_nulls & {"bill_date"}),
         "document date"),
    ]
    for should_be_recorded, check in expectations:
        if should_be_recorded:
            assert check in unavailable, (
                f"the {check!r} check could not run but nothing recorded it; "
                f"recorded={sorted(unavailable)} rx_nulls={sorted(rx_nulls)} "
                f"bill_nulls={sorted(bill_nulls)}"
            )


@PROPERTY_SETTINGS
@given(subsets, bill_subsets, doc_subsets, billdoc_subsets, test_subsets, billtest_subsets)
def test_checks_unavailable_counts_match_the_findings(
    rx_nulls: frozenset[str],
    bill_nulls: frozenset[str],
    doc_nulls: frozenset[str],
    billdoc_nulls: frozenset[str],
    test_nulls: frozenset[str],
    billtest_nulls: frozenset[str],
) -> None:
    """The headline count must equal the findings it summarises."""
    result = build(rx_nulls, bill_nulls, doc_nulls, billdoc_nulls, test_nulls, billtest_nulls)
    expected = sum(1 for f in result.findings if f.rule_code == CHECK_UNAVAILABLE_CODE)
    assert result.review_summary.checks_unavailable == expected


# --------------------------------------------------------------------------
# The specific regressions the matrix found
# --------------------------------------------------------------------------


def test_baseline_is_clean() -> None:
    """The pair the properties null out must itself reconcile perfectly."""
    result = build(frozenset(), frozenset(), frozenset(), frozenset())
    assert result.verdict == "match"
    assert result.score == 100.0
    assert result.findings == []
    assert result.review_summary.checks_unavailable == 0


@pytest.mark.parametrize("side", ["rx", "bill"])
def test_unidentifiable_line_is_not_a_critical_claim(side: str) -> None:
    """An illegible drug name cannot support 'this was not dispensed'."""
    rx_nulls = frozenset({"drug_name", "salt"}) if side == "rx" else frozenset()
    bill_nulls = frozenset() if side == "rx" else frozenset({"drug_name", "salt"})
    rx = {**RX_FULL, **dict.fromkeys(rx_nulls), "raw_text": "T. ???? ???"}
    bl = {**BILL_FULL, **dict.fromkeys(bill_nulls), "raw_text": "???? ???"}
    result = engine.reconcile(
        Prescription(overall_legibility=0.9, run_item_counts=[3, 3, 3],
                     items=[PrescribedItem(**rx)]),
        PharmacyBill(run_item_counts=[3, 3, 3], items=[BilledItem(**bl)]),
        processing_ms=0,
    )
    assert not criticals(result)
    assert result.review_summary.checks_unavailable > 0


def test_null_strength_no_longer_suppresses_brand_substitution() -> None:
    """The sample-03 failure class, for a null value rather than a null unit."""
    rx = {**RX_FULL, "drug_name": "Dolo", "strength_value": None, "strength_unit": None}
    bl = {**BILL_FULL, "drug_name": "Calpol", "strength_value": None, "strength_unit": None}
    result = engine.reconcile(
        Prescription(overall_legibility=0.9, run_item_counts=[3, 3, 3],
                     items=[PrescribedItem(**rx)]),
        PharmacyBill(run_item_counts=[3, 3, 3], items=[BilledItem(**bl)]),
        processing_ms=0,
    )
    substitution = next(f for f in result.findings if f.rule_code == "BRAND_SUBSTITUTION")
    assert substitution.detail["strength_verified"] is False


def test_schedule_h_fires_for_a_generic_name() -> None:
    """A salt-level match must not make the schedule rule inert."""
    result = engine.reconcile(
        Prescription(overall_legibility=0.9, run_item_counts=[3, 3, 3],
                     items=[PrescribedItem(item_id="rx-01", raw_text="Tab Dolo",
                                           drug_name="Dolo", confidence=0.9)]),
        PharmacyBill(
            run_item_counts=[3, 3, 3],
            items=[BilledItem(item_id="bill-01", raw_text="ALPRAZOLAM 0.5MG",
                              drug_name="Alprazolam", strength_value=0.5,
                              strength_unit="mg", form="tablet", confidence=0.9)],
        ),
        processing_ms=0,
    )
    finding = next(f for f in result.findings if f.rule_code == "SCHEDULE_H_UNBACKED")
    assert finding.detail["schedule"] in {"H", "H1", "X"}


def test_null_dose_does_not_default_to_one() -> None:
    """Assuming a dose the page does not state hid a real QUANTITY_SHORT.

    Uses a Latin frequency: BD says twice daily and nothing about units, so the
    dose is genuinely required. (Positional notation is the documented exception,
    covered separately.)
    """
    rx = {**RX_FULL, "frequency_raw": "BD", "dose_per_administration": None}
    result = engine.reconcile(
        Prescription(overall_legibility=0.9, run_item_counts=[3, 3, 3],
                     items=[PrescribedItem(**rx)]),
        PharmacyBill(run_item_counts=[3, 3, 3], items=[BilledItem(**BILL_FULL)]),
        processing_ms=0,
    )
    unavailable = [f for f in result.findings if f.rule_code == CHECK_UNAVAILABLE_CODE]
    assert any("dose per administration" in str(f.detail["missing"]) for f in unavailable)


def test_positional_frequency_does_not_need_a_stated_dose() -> None:
    """'1-0-1' encodes the dose in its slots, so the quantity check can run."""
    rx = {**RX_FULL, "frequency_raw": "1 — 0 — 1", "dose_per_administration": None}
    result = engine.reconcile(
        Prescription(overall_legibility=0.9, run_item_counts=[3, 3, 3],
                     items=[PrescribedItem(**rx)]),
        PharmacyBill(run_item_counts=[3, 3, 3], items=[BilledItem(**BILL_FULL)]),
        processing_ms=0,
    )
    quantity_blocked = [
        f for f in result.findings
        if f.rule_code == CHECK_UNAVAILABLE_CODE and f.detail.get("check") == "quantity"
    ]
    assert not quantity_blocked


def test_latin_code_without_a_dose_still_blocks_the_check() -> None:
    """'BD' says twice daily and nothing about units, so the dose is genuinely needed."""
    rx = {**RX_FULL, "frequency_raw": "BD", "dose_per_administration": None}
    result = engine.reconcile(
        Prescription(overall_legibility=0.9, run_item_counts=[3, 3, 3],
                     items=[PrescribedItem(**rx)]),
        PharmacyBill(run_item_counts=[3, 3, 3], items=[BilledItem(**BILL_FULL)]),
        processing_ms=0,
    )
    assert any(
        f.rule_code == CHECK_UNAVAILABLE_CODE and f.detail.get("check") == "quantity"
        for f in result.findings
    )
