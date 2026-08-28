"""Lab test reconciliation: panel decomposition and the five test rules.

The two tests that matter most here are the guard cases at the bottom. Both
describe the same failure -- one unreadable line becoming several confident
accusations -- and both were designed against rather than found afterwards.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from rxconcile.models import (
    BilledTest,
    PharmacyBill,
    PrescribedTest,
    Prescription,
    ReconciliationResult,
)
from rxconcile.models.schema import CHECK_UNAVAILABLE_CODE
from rxconcile.normalize import lab_panels
from rxconcile.reconcile import engine

LFT_COMPONENTS = ["SGPT", "SGOT", "Bilirubin Total", "Bilirubin Direct",
                  "Alkaline Phosphatase", "Total Protein", "Albumin"]


def prescription(
    *names: str, present: bool | None = True, legibility: float = 0.9
) -> Prescription:
    return Prescription(
        overall_legibility=legibility,
        investigations_present=present,
        tests=[
            PrescribedTest(
                item_id=f"test-{index:02d}", raw_text=f"Adv: {name}",
                test_name=name, confidence=0.9,
            )
            for index, name in enumerate(names, start=1)
        ],
    )


def bill(*names: str) -> PharmacyBill:
    return PharmacyBill(
        currency="INR",
        tests=[
            BilledTest(
                item_id=f"billtest-{index:02d}", raw_text=f"{name} .... 250.00",
                test_name=name, line_total=Decimal("250.00"), confidence=0.9,
            )
            for index, name in enumerate(names, start=1)
        ],
    )


def run(rx: Prescription, bl: PharmacyBill) -> ReconciliationResult:
    return engine.reconcile(rx, bl, processing_ms=0)


def codes(result: ReconciliationResult, code: str) -> list[str]:
    return [f.severity for f in result.findings if f.rule_code == code]


# ---------------------------------------------------------------------------
# Panel dictionary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "canonical"),
    [
        ("LFT", "Liver Function Test"),
        ("lft", "Liver Function Test"),
        ("Liver Function Test", "Liver Function Test"),
        ("KFT", "Kidney Function Test"),
        ("RFT", "Kidney Function Test"),
        ("CBC", "Complete Blood Count"),
        ("Haemogram", "Complete Blood Count"),
        ("Lipid Profile", "Lipid Profile"),
        ("TFT", "Thyroid Profile"),
        ("Urine R/M", "Urine Routine and Microscopy"),
        ("HbA1c", "HbA1c"),
    ],
)
def test_panel_names_resolve(written: str, canonical: str) -> None:
    match = lab_panels.resolve(written)
    assert match.kind == "panel"
    assert match.name == canonical
    assert match.components


@pytest.mark.parametrize(
    ("written", "canonical"),
    [("SGPT", "SGPT"), ("ALT", "SGPT"), ("SGOT", "SGOT"), ("AST", "SGOT"),
     ("Hb", "Haemoglobin"), ("TLC", "Total WBC Count"), ("PCV", "Packed Cell Volume"),
     ("HDL", "HDL Cholesterol"), ("Serum Creatinine", "Creatinine")],
)
def test_analyte_aliases_resolve(written: str, canonical: str) -> None:
    match = lab_panels.resolve(written)
    assert match.kind == "test"
    assert match.name == canonical


def test_unknown_name_is_unresolved_with_no_components() -> None:
    """The empty expansion that the engine must never read as 'covers nothing'."""
    match = lab_panels.resolve("Zzz Quantum Profile")
    assert not match.resolved
    assert match.components == ()


def test_a_weak_similarity_is_not_forced_into_a_match() -> None:
    assert not lab_panels.resolve("LF").resolved
    assert not lab_panels.resolve("Profile").resolved


# ---------------------------------------------------------------------------
# Panel decomposition -- the case the feature exists for
# ---------------------------------------------------------------------------


def test_panel_ordered_and_itemised_on_the_bill_is_a_match() -> None:
    """LFT ordered, seven analytes billed. One order, not four accusations."""
    result = run(prescription("LFT"), bill(*LFT_COMPONENTS))
    assert result.verdict == "match"
    assert result.score == 100.0
    assert not [f for f in result.findings if f.rule_code.startswith("TEST_")]
    assert not [f for f in result.findings if f.rule_code == "PANEL_PARTIAL"]
    assert len(result.matched_tests) == 1
    assert result.matched_tests[0].prescribed_id == "test-01"


def test_panel_billed_as_the_panel_is_also_a_match() -> None:
    result = run(prescription("LFT"), bill("Liver Function Test"))
    assert result.verdict == "match"
    assert len(result.matched_tests) == 1


def test_components_ordered_individually_match_component_billing() -> None:
    result = run(prescription("SGPT", "SGOT"), bill("ALT", "AST"))
    assert result.verdict == "match"
    assert len(result.matched_tests) == 2


def test_panel_decomposition_does_not_leak_across_panels() -> None:
    """A billed LFT analyte must not satisfy an ordered CBC."""
    result = run(prescription("CBC"), bill("SGPT"))
    assert codes(result, "TEST_NOT_BILLED") == ["critical"]
    assert codes(result, "TEST_NOT_PRESCRIBED") == ["critical"]


# ---------------------------------------------------------------------------
# One test per rule code
# ---------------------------------------------------------------------------


def test_test_not_billed_is_critical() -> None:
    result = run(prescription("CBC", "HbA1c"), bill("CBC"))
    assert codes(result, "TEST_NOT_BILLED") == ["critical"]
    assert result.verdict == "mismatch"
    assert result.unmatched_prescribed_tests == ["test-02"]


def test_test_not_prescribed_is_critical() -> None:
    result = run(prescription("CBC"), bill("CBC", "Lipid Profile"))
    assert codes(result, "TEST_NOT_PRESCRIBED") == ["critical"]
    assert result.verdict == "mismatch"
    assert result.unmatched_billed_tests == ["billtest-02"]


def test_panel_partial_is_a_warning_naming_the_missing_components() -> None:
    result = run(prescription("LFT"), bill("SGPT", "SGOT"))
    partial = [f for f in result.findings if f.rule_code == "PANEL_PARTIAL"]
    assert len(partial) == 1
    assert partial[0].severity == "warning"
    missing = partial[0].detail["missing_components"]
    assert "Albumin" in missing and "Bilirubin Total" in missing
    assert "SGPT" not in missing
    # The reader is told which components ARE covered, not only which are not.
    assert sorted(partial[0].detail["billed_components"]) == ["SGOT", "SGPT"]
    assert result.verdict == "match_with_warnings"


def test_test_duplicate_is_a_warning() -> None:
    result = run(prescription("CBC"), bill("CBC", "Complete Blood Count"))
    assert codes(result, "TEST_DUPLICATE") == ["warning"]
    assert result.verdict == "match_with_warnings"


def test_test_unresolved_is_info_on_both_sides() -> None:
    result = run(prescription("Qqq Panel"), bill("Www Assay"))
    unresolved = [f for f in result.findings if f.rule_code == "TEST_UNRESOLVED"]
    assert len(unresolved) == 2
    assert {f.severity for f in unresolved} == {"info"}
    assert {f.detail["side"] for f in unresolved} == {"prescription", "bill"}


# ---------------------------------------------------------------------------
# Absence is not a discrepancy
# ---------------------------------------------------------------------------


def test_no_tests_on_either_side_produces_no_test_findings() -> None:
    result = run(prescription(present=False), bill())
    assert not [f for f in result.findings if "TEST" in f.rule_code
                or f.rule_code == "PANEL_PARTIAL"]
    assert result.matched_tests == []


def test_a_prescription_with_no_tests_is_not_penalised() -> None:
    """The common case: a prescription that orders only medicines."""
    result = run(prescription(present=False), bill())
    assert result.verdict == "match"
    assert result.score == 100.0


def test_medicine_only_bill_against_test_only_prescription_still_reports_tests() -> None:
    """Lab and pharmacy bills are separate documents; the schema allows either."""
    rx = prescription("LFT")
    assert not rx.items
    result = run(rx, bill())
    assert codes(result, "TEST_NOT_BILLED") == ["critical"]


# ---------------------------------------------------------------------------
# Guard case A -- present but unreadable is not absent
# ---------------------------------------------------------------------------


def test_an_unreadable_orders_section_does_not_read_as_no_tests_ordered() -> None:
    result = run(prescription(present=True), bill("SGPT", "SGOT"))
    severities = codes(result, "TEST_NOT_PRESCRIBED")
    assert severities == ["warning", "warning"], "accusations must not be confident"
    assert result.verdict != "mismatch"
    # And the reason is stated, not implied by a missing finding.
    unavailable = [
        f for f in result.findings
        if f.rule_code == CHECK_UNAVAILABLE_CODE
        and f.detail.get("check") == "test authorisation"
    ]
    assert unavailable, "the reviewer must be told the orders could not be read"
    assert "could not be read" in unavailable[0].message


def test_a_confirmed_absent_orders_section_does_produce_criticals() -> None:
    """The other half: absence, once confirmed, is a real discrepancy."""
    result = run(prescription(present=False), bill("SGPT"))
    assert codes(result, "TEST_NOT_PRESCRIBED") == ["critical"]
    assert result.verdict == "mismatch"


def test_an_unconfirmed_orders_section_is_treated_as_uncertain_not_absent() -> None:
    result = run(prescription(present=None), bill("SGPT"))
    assert codes(result, "TEST_NOT_PRESCRIBED") == ["warning"]


# ---------------------------------------------------------------------------
# Guard case B -- an unresolved panel expands to nothing, not to zero
# ---------------------------------------------------------------------------


def test_an_unresolvable_panel_does_not_accuse_every_billed_component() -> None:
    """One illegible order line must not become four confident accusations."""
    result = run(prescription("Xyzzy [?] Profile"),
                 bill("SGPT", "SGOT", "Bilirubin Total", "Alkaline Phosphatase"))

    assert codes(result, "TEST_NOT_PRESCRIBED") == ["warning"] * 4
    assert result.verdict != "mismatch"
    assert not [f for f in result.findings if f.severity == "critical"]

    # The unresolved order is reported as unresolved, not silently dropped,
    # and it is never reported as a test that was ordered and not billed.
    assert codes(result, "TEST_UNRESOLVED") == ["info"]
    assert not codes(result, "TEST_NOT_BILLED")

    # Every accusation says why it is not confident.
    for found in result.findings:
        if found.rule_code == "TEST_NOT_PRESCRIBED":
            assert found.detail["softened_because"]
            assert "could not be identified" in found.message


def test_an_unresolvable_billed_line_softens_a_missing_test() -> None:
    """The mirror image, on the bill side."""
    result = run(prescription("CBC"), bill("Www [?] Assay"))
    assert codes(result, "TEST_NOT_BILLED") == ["warning"]
    assert not [f for f in result.findings if f.severity == "critical"]


def test_an_unresolved_line_records_that_a_check_could_not_run() -> None:
    result = run(prescription("Qqq Panel"), bill("Www Assay"))
    checks = [
        f for f in result.findings
        if f.rule_code == CHECK_UNAVAILABLE_CODE
        and f.detail.get("check") in {"test billing", "test authorisation"}
    ]
    assert len(checks) >= 2
    assert result.review_summary.checks_unavailable >= 2


# ---------------------------------------------------------------------------
# Referential integrity
# ---------------------------------------------------------------------------


def test_every_test_finding_reference_resolves() -> None:
    result = run(prescription("LFT", "Qqq Panel"), bill("SGPT", "Lipid Profile"))
    rx_ids = result.prescription.item_ids
    bill_ids = result.bill.item_ids
    for found in result.findings:
        if found.prescribed_ref is not None:
            assert found.prescribed_ref in rx_ids
        if found.billed_ref is not None:
            assert found.billed_ref in bill_ids


def test_test_ids_and_item_ids_share_one_namespace_without_collision() -> None:
    rx = prescription("CBC")
    assert rx.test_ids == {"test-01"}
    assert "test-01" in rx.item_ids


def test_a_test_billed_with_quantity_two_is_a_duplicate() -> None:
    """The other shape of repeat billing: one line, quantity above one."""
    bl = PharmacyBill(
        currency="INR",
        tests=[BilledTest(item_id="billtest-01", raw_text="CBC x2", test_name="CBC",
                          quantity=2.0, confidence=0.9)],
    )
    result = run(prescription("CBC"), bl)
    duplicates = [f for f in result.findings if f.rule_code == "TEST_DUPLICATE"]
    assert len(duplicates) == 1
    assert duplicates[0].severity == "warning"
    assert duplicates[0].detail["quantity"] == 2.0


def test_a_missing_quantity_records_that_repeat_billing_was_unchecked() -> None:
    """Null quantity must not read as 'checked, billed once'."""
    result = run(prescription("CBC"), bill("CBC"))
    checks = [
        f for f in result.findings
        if f.rule_code == CHECK_UNAVAILABLE_CODE
        and f.detail.get("check") == "repeat test billing"
    ]
    assert len(checks) == 1
    assert not [f for f in result.findings if f.rule_code == "TEST_DUPLICATE"]


def test_quantity_of_one_is_not_a_duplicate() -> None:
    bl = PharmacyBill(
        currency="INR",
        tests=[BilledTest(item_id="billtest-01", raw_text="CBC", test_name="CBC",
                          quantity=1.0, confidence=0.9)],
    )
    result = run(prescription("CBC"), bl)
    assert not [f for f in result.findings if f.rule_code == "TEST_DUPLICATE"]
    assert result.verdict == "match"


# ---------------------------------------------------------------------------
# Lab bills and pharmacy bills are separate documents
# ---------------------------------------------------------------------------


def test_a_pharmacy_only_bill_does_not_accuse_ordered_tests() -> None:
    """A bill with no lab lines is not evidence that the tests were skipped."""
    from rxconcile.models import BilledItem

    bl = PharmacyBill(
        currency="INR",
        items=[BilledItem(item_id="bill-01", raw_text="DOLO 650", drug_name="Dolo",
                          confidence=0.9)],
    )
    result = run(prescription("CBC"), bl)
    assert codes(result, "TEST_NOT_BILLED") == ["warning"]
    assert "separate document" in next(
        f.message for f in result.findings if f.rule_code == "TEST_NOT_BILLED"
    )


def test_a_lab_only_bill_does_not_accuse_prescribed_medicines() -> None:
    """The mirror image: a lab bill is not evidence a medicine went undispensed."""
    from rxconcile.models import PrescribedItem

    rx = Prescription(
        overall_legibility=0.9,
        investigations_present=True,
        items=[PrescribedItem(item_id="rx-01", raw_text="Tab. Dolo 650", drug_name="Dolo",
                              confidence=0.9)],
        tests=[PrescribedTest(item_id="test-01", raw_text="Adv: CBC", test_name="CBC",
                              confidence=0.9)],
    )
    result = run(rx, bill("CBC"))
    not_billed = [f for f in result.findings if f.rule_code == "RX_NOT_BILLED"]
    assert [f.severity for f in not_billed] == ["warning"]
    assert not_billed[0].detail["lab_only_bill"] is True
    assert result.verdict != "mismatch"
