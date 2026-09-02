"""The report axis: ordered, billed, reported.

Every test here compares which tests appear on which document. **None of them
reads a result**, and that is the point -- hard rule 10 puts interpretation out
of scope, so the rules must be expressible without it.
"""

from __future__ import annotations

import datetime as dt

from rxconcile.models import (
    BilledTest,
    LabReport,
    PharmacyBill,
    PrescribedTest,
    Prescription,
    ReportedTest,
)
from rxconcile.reconcile.report import reconcile_report


def rx(*tests: str, date: dt.date | None = None, patient: str | None = None) -> Prescription:
    return Prescription(
        patient_name=patient,
        date_issued=date,
        overall_legibility=0.9,
        investigations_present=bool(tests),
        tests=[
            PrescribedTest(item_id=f"test-{i:02d}", raw_text=name, test_name=name, confidence=0.9)
            for i, name in enumerate(tests, 1)
        ],
    )


def bill(*tests: str) -> PharmacyBill:
    return PharmacyBill(
        tests=[
            BilledTest(item_id=f"billtest-{i:02d}", raw_text=n, test_name=n, confidence=0.9)
            for i, n in enumerate(tests, 1)
        ],
    )


def report(
    *tests: str, patient: str | None = None, collected: dt.date | None = None
) -> LabReport:
    return LabReport(
        patient_name=patient,
        collected_date=collected,
        tests=[
            ReportedTest(
                item_id=f"reptest-{i:02d}", raw_text=n, test_name=n,
                result_value="1.0", confidence=0.9,
            )
            for i, n in enumerate(tests, 1)
        ],
    )


def codes(outcome: object) -> set[str]:
    return {f.rule_code for f in outcome.findings}  # type: ignore[attr-defined]


class TestAMissingReportIsNotApplicable:
    def test_no_report_produces_no_findings_at_all(self) -> None:
        """Not a softened warning, not an unavailable check. Nothing.

        Nobody is obliged to upload a report. Reporting its absence as a
        shortfall would invent a requirement the product does not have.
        """
        outcome = reconcile_report(
            rx("HbA1c"), bill("HbA1c"), None, lab_bill_supplied=True
        )
        assert outcome.findings == []
        assert outcome.reported == set()


class TestBilledButNotReported:
    def test_a_charge_with_no_result_is_critical(self) -> None:
        """The rule the report axis exists for."""
        outcome = reconcile_report(
            rx("HbA1c", "Lipid Profile"),
            bill("HbA1c", "Lipid Profile"),
            report("HbA1c"),
            lab_bill_supplied=True,
        )
        assert "TEST_BILLED_NOT_REPORTED" in codes(outcome)

    def test_no_lab_bill_means_the_check_could_not_run(self) -> None:
        """Absent charges are not silent clearance.

        With no lab bill there is nothing to check the report against, and
        saying so is the only honest answer -- a check that could not run must
        never render as one that passed.
        """
        outcome = reconcile_report(
            rx("HbA1c"), bill(), report("HbA1c"), lab_bill_supplied=False
        )
        assert "TEST_BILLED_NOT_REPORTED" not in codes(outcome)
        assert "CHECK_UNAVAILABLE" in codes(outcome)

    def test_a_panel_billed_as_one_line_covers_its_components(self) -> None:
        """Reuses lab_panels, so a panel charge is not five missing results."""
        outcome = reconcile_report(
            rx("Lipid Profile"),
            bill("Lipid Profile"),
            report(
                "Total Cholesterol", "Triglycerides", "HDL Cholesterol",
                "LDL Cholesterol", "VLDL Cholesterol",
            ),
            lab_bill_supplied=True,
        )
        assert "TEST_BILLED_NOT_REPORTED" not in codes(outcome)


class TestReportedButNotBilled:
    def test_it_is_information_not_a_problem(self) -> None:
        """A laboratory routinely reports more than a bill itemises."""
        outcome = reconcile_report(
            rx("HbA1c"), bill("HbA1c"), report("HbA1c", "Cortisol"),
            lab_bill_supplied=True,
        )
        severities = {f.rule_code: f.severity for f in outcome.findings}
        assert severities.get("TEST_REPORTED_NOT_BILLED") == "info"


class TestReportedButNotOrdered:
    def test_a_result_nobody_asked_for_is_a_warning(self) -> None:
        outcome = reconcile_report(
            rx("HbA1c"), bill("HbA1c"), report("HbA1c", "Cortisol"),
            lab_bill_supplied=True,
        )
        severities = {f.rule_code: f.severity for f in outcome.findings}
        assert severities.get("TEST_REPORTED_NOT_ORDERED") == "warning"

    def test_an_unreadable_investigations_section_does_not_accuse(self) -> None:
        """The real false-positive risk.

        A prescription whose orders could not be read would otherwise turn every
        single result into a finding -- accusations manufactured from a document
        nobody could read.
        """
        blind = Prescription(overall_legibility=0.4, investigations_present=True, tests=[])
        outcome = reconcile_report(
            blind, bill(), report("Cortisol", "Vitamin B12"), lab_bill_supplied=False
        )
        assert "TEST_REPORTED_NOT_ORDERED" not in codes(outcome)
        assert "CHECK_UNAVAILABLE" in codes(outcome)

    def test_a_panel_order_covers_every_component_reported(self) -> None:
        """One written order, two results on two pages, no findings."""
        outcome = reconcile_report(
            rx("Plasma glucose (F / PP)"),
            bill(),
            report("Glucose Fasting", "Glucose (PP)"),
            lab_bill_supplied=False,
        )
        assert "TEST_REPORTED_NOT_ORDERED" not in codes(outcome)


class TestPatientIdentity:
    def test_a_different_person_is_flagged(self) -> None:
        outcome = reconcile_report(
            rx("HbA1c", patient="Ishan Roy"), bill(),
            report("HbA1c", patient="Sunita Sharma"), lab_bill_supplied=False,
        )
        assert "REPORT_PATIENT_MISMATCH" in codes(outcome)

    def test_titles_and_case_are_not_a_mismatch(self) -> None:
        """"Mr. ISHAN ROY" and "Ishan Roy" are the same man.

        The rule exists to catch somebody else's report attached to a claim, not
        to police transliteration between a handwritten page and a printed one.
        """
        outcome = reconcile_report(
            rx("HbA1c", patient="Ishan Roy"), bill(),
            report("HbA1c", patient="Mr. ISHAN ROY"), lab_bill_supplied=False,
        )
        assert "REPORT_PATIENT_MISMATCH" not in codes(outcome)

    def test_a_missing_name_is_not_a_mismatch(self) -> None:
        outcome = reconcile_report(
            rx("HbA1c", patient=None), bill(),
            report("HbA1c", patient="Mr. ISHAN ROY"), lab_bill_supplied=False,
        )
        assert "REPORT_PATIENT_MISMATCH" not in codes(outcome)


class TestTiming:
    def test_collected_well_before_the_prescription_is_flagged(self) -> None:
        outcome = reconcile_report(
            rx("HbA1c", date=dt.date(2026, 3, 14)), bill(),
            report("HbA1c", collected=dt.date(2026, 1, 5)), lab_bill_supplied=False,
        )
        assert "REPORT_DATE_ANOMALY" in codes(outcome)

    def test_collected_after_the_prescription_is_ordinary(self) -> None:
        outcome = reconcile_report(
            rx("HbA1c", date=dt.date(2024, 9, 30)), bill(),
            report("HbA1c", collected=dt.date(2026, 3, 14)), lab_bill_supplied=False,
        )
        assert "REPORT_DATE_ANOMALY" not in codes(outcome)

    def test_same_day_collection_is_ordinary(self) -> None:
        """A sample taken the morning the prescription was written."""
        day = dt.date(2026, 3, 14)
        outcome = reconcile_report(
            rx("HbA1c", date=day), bill(), report("HbA1c", collected=day),
            lab_bill_supplied=False,
        )
        assert "REPORT_DATE_ANOMALY" not in codes(outcome)

    def test_a_missing_date_records_that_the_check_could_not_run(self) -> None:
        outcome = reconcile_report(
            rx("HbA1c", date=None), bill(), report("HbA1c", collected=None),
            lab_bill_supplied=False,
        )
        assert "REPORT_DATE_ANOMALY" not in codes(outcome)
        assert "CHECK_UNAVAILABLE" in codes(outcome)


def test_no_rule_reads_a_result() -> None:
    """The hard-rule-10 guard.

    Two reports identical but for their VALUES must produce identical findings.
    If any rule ever branches on a measurement, this fails -- which is the whole
    point of having it.
    """
    low = LabReport(tests=[ReportedTest(
        item_id="reptest-01", raw_text="Vitamin D", test_name="Vitamin D",
        result_value="3.00", reference_range="75.00 - 250.00", confidence=0.9)])
    high = LabReport(tests=[ReportedTest(
        item_id="reptest-01", raw_text="Vitamin D", test_name="Vitamin D",
        result_value="900.00", reference_range="75.00 - 250.00", confidence=0.9)])

    a = reconcile_report(rx("Vitamin D"), bill("Vitamin D"), low, lab_bill_supplied=True)
    b = reconcile_report(rx("Vitamin D"), bill("Vitamin D"), high, lab_bill_supplied=True)
    assert [f.model_dump() for f in a.findings] == [f.model_dump() for f in b.findings]
