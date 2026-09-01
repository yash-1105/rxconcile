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
        currency="INR", pharmacy_licence_no="TN/2019/337821",
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


def test_an_UNREADABLE_billed_line_softens_a_missing_test() -> None:
    """A line with no name read off it might be anything, including the CBC."""
    illegible = PharmacyBill(
        currency="INR", pharmacy_licence_no="TN/2019/337821",
        tests=[BilledTest(item_id="billtest-01", raw_text="~~ smudge ~~",
                          test_name=None, line_total=Decimal("250.00"), confidence=0.4)],
    )
    result = engine.reconcile(prescription("CBC"), illegible, processing_ms=0)
    assert codes(result, "TEST_NOT_BILLED") == ["warning"]
    found = next(f for f in result.findings if f.rule_code == "TEST_NOT_BILLED")
    assert found.detail["softened_code"] == "unidentified_billed_lines"


def test_a_LEGIBLE_but_unrecognised_billed_line_does_not_soften_a_missing_test() -> None:
    """The defect this replaces.

    "Vitamin D (25-OH)" is read perfectly and is simply absent from
    lab_panels. It is a known, different test, so it cannot be the missing one
    — and reporting it as "some billed lab lines could not be read" was false
    about a bill every line of which had been read.
    """
    result = run(prescription("KFT"), bill("Vitamin D (25-OH)"))
    assert codes(result, "TEST_NOT_BILLED") == ["critical"]
    found = next(f for f in result.findings if f.rule_code == "TEST_NOT_BILLED")
    assert found.detail["softened_code"] is None
    assert found.detail["softened_because"] is None


def test_a_legible_billed_test_the_dictionary_never_saw_is_still_unordered() -> None:
    """Resolution is needed to MATCH, not to observe absence.

    An unresolved billed line used to emit TEST_UNRESOLVED and `continue`, so a
    legible test that nobody ordered was never reported as unordered at all.
    Deliberately a name this build has never heard of: the rule has to hold for
    tests our reference data does not cover, which is the whole point.
    """
    result = run(prescription("KFT"), bill("Serum Zonulin Assay"))
    assert codes(result, "TEST_NOT_PRESCRIBED") == ["critical"]
    found = next(f for f in result.findings if f.rule_code == "TEST_NOT_PRESCRIBED")
    assert found.detail["resolved_as"] == "Serum Zonulin Assay"
    assert found.detail["identified"] is False
    # The dictionary gap is still stated, as an info note beside it.
    assert codes(result, "TEST_UNRESOLVED") == ["info"]


def test_an_unreadable_billed_line_is_never_accused_of_being_unordered() -> None:
    """Nothing was read off it, so nothing can be said about it."""
    illegible = PharmacyBill(
        currency="INR", pharmacy_licence_no="TN/2019/337821",
        tests=[BilledTest(item_id="billtest-01", raw_text="~~ smudge ~~",
                          test_name=None, line_total=Decimal("250.00"), confidence=0.4)],
    )
    result = engine.reconcile(prescription(present=False), illegible, processing_ms=0)
    assert not codes(result, "TEST_NOT_PRESCRIBED")
    assert [f for f in result.findings if f.rule_code == CHECK_UNAVAILABLE_CODE]


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
        currency="INR", pharmacy_licence_no="TN/2019/337821",
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
        currency="INR", pharmacy_licence_no="TN/2019/337821",
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
        currency="INR", pharmacy_licence_no="TN/2019/337821",
        items=[BilledItem(item_id="bill-01", raw_text="DOLO 650", drug_name="Dolo",
                          confidence=0.9)],
    )
    result = run(prescription("CBC"), bl)
    assert codes(result, "TEST_NOT_BILLED") == ["warning"]
    assert "no lab bill was uploaded" in next(
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


# ---------------------------------------------------------------------------
# Panel/component notation, as laboratories actually print it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "canonical"),
    [
        ("Lipid Profile — Total Cholesterol", "Total Cholesterol"),
        ("Lipid Profile – HDL", "HDL Cholesterol"),
        ("Lipid Profile - LDL", "LDL Cholesterol"),
        ("Lipid Profile: Triglycerides", "Triglycerides"),
        ("Liver Function Test — SGPT", "SGPT"),
    ],
)
def test_a_panel_component_line_resolves_to_the_component(
    written: str, canonical: str
) -> None:
    """The bill charged for one analyte, so the line is that analyte.

    Resolving it to the whole panel would satisfy an order six lines early.
    """
    match = lab_panels.resolve(written)
    assert match.kind == "test"
    assert match.name == canonical


@pytest.mark.parametrize(
    ("written", "canonical"),
    [
        ("Thyroid Profile (T3, T4, TSH)", "Thyroid Profile"),
        ("Lipid Profile (Total Cholesterol, HDL, LDL)", "Lipid Profile"),
        ("Complete Blood Count [CBC]", "Complete Blood Count"),
    ],
)
def test_a_parenthetical_component_list_is_stripped_before_lookup(
    written: str, canonical: str
) -> None:
    match = lab_panels.resolve(written)
    assert match.kind == "panel"
    assert match.name == canonical


def test_a_panel_with_an_unreadable_component_stays_unresolved() -> None:
    """A panel line with an unreadable analyte is not the whole panel."""
    match = lab_panels.resolve("Lipid Profile — Zzz Unknown Analyte")
    assert not match.resolved
    assert match.components == ()


def test_compound_parsing_never_loosens_an_unknown_name() -> None:
    for written in ("Qqq Panel — Www Assay", "Zzz — Yyy", "Not A Test At All"):
        assert not lab_panels.resolve(written).resolved


def test_a_hyphenated_name_is_not_torn_in_half() -> None:
    """A plain hyphen splits only when spaced."""
    assert lab_panels.resolve("Urine R/M").name == "Urine Routine and Microscopy"
    assert lab_panels.resolve("HbA1c").name == "HbA1c"


# ---------------------------------------------------------------------------
# The Apollo/Sri Balaji pair, end to end
# ---------------------------------------------------------------------------


def test_the_real_pair_reconciles_correctly() -> None:
    """Lipid Profile ordered and billed as four analytes; two orders missing.

    Reproduces the pair that was reporting eight rows and zero findings.
    """
    rx = prescription("Lipid Profile", "HbA1c", "Serum Creatinine")
    bl = bill(
        "Lipid Profile — Total Cholesterol",
        "Lipid Profile — HDL",
        "Lipid Profile — LDL",
        "Lipid Profile — Triglycerides",
        "Thyroid Profile (T3, T4, TSH)",
    )
    result = run(rx, bl)

    assert codes(result, "TEST_NOT_BILLED") == ["critical", "critical"], (
        "HbA1c and Serum Creatinine were ordered and not billed"
    )
    assert codes(result, "TEST_NOT_PRESCRIBED") == ["critical"], "Thyroid Profile"
    assert not codes(result, "TEST_UNRESOLVED"), "every billed line is recognisable"
    assert not codes(result, "PANEL_PARTIAL"), "all four lipid components were billed"

    # The panel decomposition case: one matched pair, no finding against it.
    assert len(result.matched_tests) == 1
    assert result.matched_tests[0].prescribed_id == "test-01"

    not_billed = {
        f.detail["resolved_as"] for f in result.findings if f.rule_code == "TEST_NOT_BILLED"
    }
    assert not_billed == {"HbA1c", "Creatinine"}


def test_no_finding_on_this_pair_claims_a_missing_lab_bill() -> None:
    """THE REGRESSION GUARD: a bill with lab lines is never a missing lab bill."""
    rx = prescription("Lipid Profile", "HbA1c", "Serum Creatinine")
    bl = bill("Lipid Profile — HDL", "Thyroid Profile (T3, T4, TSH)")
    result = run(rx, bl)
    for found in result.findings:
        assert found.detail.get("softened_code") != "no_lab_bill"


def test_a_bill_with_any_lab_line_is_never_treated_as_a_missing_lab_bill() -> None:
    """Even when nothing on it can be identified.

    The screen once read "no lab bill supplied" against a bill carrying five
    lab lines. Unreadable is not absent.
    """
    from rxconcile.models import BilledItem

    for billed_names in (
        ["Zzz Unknown Assay"],
        ["Qqq Panel — Www Assay", "Another Unknown"],
        ["CBC"],
    ):
        bl = bill(*billed_names)
        bl = PharmacyBill(
            currency="INR", pharmacy_licence_no="TN/2019/337821",
            items=[BilledItem(item_id="bill-01", raw_text="DOLO", drug_name="Dolo",
                              confidence=0.9)],
            tests=bl.tests,
        )
        result = run(prescription("HbA1c"), bl)
        reasons = {f.detail.get("softened_code") for f in result.findings}
        assert "no_lab_bill" not in reasons, f"failed for {billed_names}"


def test_a_bill_with_no_lab_lines_at_all_still_reports_the_missing_document() -> None:
    """The other half: the guard must still fire when it genuinely applies."""
    from rxconcile.models import BilledItem

    bl = PharmacyBill(
        currency="INR", pharmacy_licence_no="TN/2019/337821",
        items=[BilledItem(item_id="bill-01", raw_text="DOLO", drug_name="Dolo",
                          confidence=0.9)],
    )
    result = run(prescription("HbA1c"), bl)
    reasons = {f.detail.get("softened_code") for f in result.findings}
    assert "no_lab_bill" in reasons


# ---------------------------------------------------------------------------
# Completeness is stated, not inferred
# ---------------------------------------------------------------------------


def test_a_supplied_lab_bill_is_never_reported_as_missing() -> None:
    """The structural fix for the false "no lab bill supplied".

    The engine used to infer this from whether the extracted bill carried lab
    lines. The operator now says which document is which.
    """
    from rxconcile.models import Submission

    stated = Submission(lab_bill_supplied=True)
    result = engine.reconcile(
        prescription("CBC"), bill("Zzz Unrecognised Assay"),
        processing_ms=0, submission=stated,
    )
    reasons = {f.detail.get("softened_code") for f in result.findings}
    assert "no_lab_bill" not in reasons


def test_an_absent_lab_bill_is_only_flagged_when_tests_were_ordered() -> None:
    """No tests ordered and no lab bill is a legitimate choice, not a gap."""
    from rxconcile.models import BilledItem, Submission

    no_lab = Submission(lab_bill_supplied=False)
    medicines_only = PharmacyBill(
        currency="INR",
        items=[BilledItem(item_id="bill-01", raw_text="DOLO", drug_name="Dolo",
                          confidence=0.9)],
    )
    quiet = engine.reconcile(
        prescription(present=False), medicines_only, processing_ms=0, submission=no_lab
    )
    assert not [f for f in quiet.findings if f.detail.get("softened_code") == "no_lab_bill"]
    assert not [f for f in quiet.findings if f.rule_code.startswith("TEST_")]

    # Tests ordered with no lab bill behind them IS a missing document.
    flagged = engine.reconcile(
        prescription("CBC"), medicines_only, processing_ms=0, submission=no_lab
    )
    assert [f for f in flagged.findings if f.detail.get("softened_code") == "no_lab_bill"]


def test_a_supplied_pharmacy_bill_never_excuses_a_missing_medicine() -> None:
    from rxconcile.models import BilledTest, PrescribedItem, Submission

    rx = Prescription(
        overall_legibility=0.9,
        items=[PrescribedItem(item_id="rx-01", raw_text="Dolo", drug_name="Dolo",
                              confidence=0.9)],
    )
    lab_lines = PharmacyBill(
        currency="INR",
        tests=[BilledTest(item_id="billtest-01", raw_text="CBC", test_name="CBC",
                          confidence=0.9)],
    )
    # The operator says a pharmacy bill WAS uploaded, so a missing medicine is
    # a real finding even though this document happens to hold only lab lines.
    stated = Submission(pharmacy_bill_supplied=True, lab_bill_supplied=True)
    result = engine.reconcile(rx, lab_lines, processing_ms=0, submission=stated)
    not_billed = [f for f in result.findings if f.rule_code == "RX_NOT_BILLED"]
    assert [f.severity for f in not_billed] == ["critical"]
    assert not_billed[0].detail["lab_only_bill"] is False


# ---------------------------------------------------------------------------
# A combined pharmacy-and-diagnostics bill
# ---------------------------------------------------------------------------


def test_lab_lines_on_the_pharmacy_bill_are_something_to_check_against() -> None:
    """The false negative this guards.

    An Indian pharmacy that is also a diagnostics centre bills medicines and lab
    work on ONE document. No separate lab bill is uploaded, so
    `lab_bill_supplied` is False -- but the lab lines are right there on the
    pharmacy bill. Reading the upload slot instead of the document softened two
    genuinely unbilled tests into warnings and reported them as "not assessed"
    against five billed lab lines sitting in the same result.
    """
    from rxconcile.models import BilledItem, BilledTest, Submission

    combined = PharmacyBill(
        currency="INR",
        pharmacy_licence_no="TN/2019/337821",
        items=[BilledItem(item_id="bill-01", raw_text="DOLO", drug_name="Dolo",
                          confidence=0.9)],
        tests=[BilledTest(item_id="billtest-01", raw_text="Lipid Profile .... 600.00",
                          test_name="Lipid Profile", line_total=Decimal("600.00"),
                          confidence=0.9)],
    )
    # The operator uploaded a prescription and a pharmacy bill. No lab bill.
    stated = Submission(
        prescription_supplied=True, pharmacy_bill_supplied=True,
        lab_report_supplied=False, lab_bill_supplied=False,
    )
    result = engine.reconcile(
        prescription("Lipid Profile", "HbA1c"), combined, processing_ms=0, submission=stated
    )
    unbilled = [f for f in result.findings if f.rule_code == "TEST_NOT_BILLED"]
    assert len(unbilled) == 1, "HbA1c is the only ordered test not on the bill"
    assert unbilled[0].severity == "critical", "it was checked, and it is absent"
    assert unbilled[0].detail.get("softened_code") is None
    assert unbilled[0].detail.get("softened_because") is None


def test_no_billed_lab_line_anywhere_is_still_softened() -> None:
    """The genuine case the softening exists for, unchanged."""
    from rxconcile.models import BilledItem, Submission

    medicines_only = PharmacyBill(
        currency="INR",
        items=[BilledItem(item_id="bill-01", raw_text="DOLO", drug_name="Dolo",
                          confidence=0.9)],
    )
    result = engine.reconcile(
        prescription("HbA1c"), medicines_only, processing_ms=0,
        submission=Submission(pharmacy_bill_supplied=True, lab_bill_supplied=False),
    )
    found = next(f for f in result.findings if f.rule_code == "TEST_NOT_BILLED")
    assert found.severity == "warning"
    assert found.detail["softened_code"] == "no_lab_bill"


def test_an_uploaded_lab_bill_with_no_readable_test_line_says_so() -> None:
    """Not 'no lab bill was supplied'. One was; it could not be read.

    A different statement, and a worse one — the document is there and the
    system could make nothing of it.
    """
    from rxconcile.models import BilledItem, Submission

    unreadable_lab_bill = PharmacyBill(
        currency="INR",
        items=[BilledItem(item_id="bill-01", raw_text="DOLO", drug_name="Dolo",
                          confidence=0.9)],
    )
    result = engine.reconcile(
        prescription("HbA1c"), unreadable_lab_bill, processing_ms=0,
        submission=Submission(pharmacy_bill_supplied=True, lab_bill_supplied=True),
    )
    found = next(f for f in result.findings if f.rule_code == "TEST_NOT_BILLED")
    assert found.severity == "warning", "still softened: there is nothing to check against"
    assert found.detail["softened_code"] == "lab_bill_unreadable"
    assert "no lab bill was uploaded" not in found.message
