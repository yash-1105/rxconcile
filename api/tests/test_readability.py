"""What the employee is told about their own documents.

The guard rail on this module is negative: it must report whether a page could
be READ and nothing else. A legible bill with a real discrepancy on it is not
something to re-photograph, and the discrepancy is not the submitter's to see.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

from rxconcile.models import (
    BilledItem,
    PharmacyBill,
    PrescribedItem,
    Prescription,
    Submission,
)
from rxconcile.reconcile import engine, readability
from rxconcile.reconcile.readability import readability_of, unavailable


def _states(result: object) -> dict[str, str]:
    return {doc.slot: doc.state for doc in readability_of(result)}  # type: ignore[arg-type]


def clean_pair() -> tuple[Prescription, PharmacyBill]:
    rx = Prescription(
        overall_legibility=0.95, investigations_present=False,
        run_item_counts=[1, 1, 1],
        items=[PrescribedItem(item_id="rx-01", raw_text="Dolo 650", drug_name="Dolo",
                              strength_value=650.0, strength_unit="mg", form="tablet",
                              confidence=0.9)],
    )
    bill = PharmacyBill(
        currency="INR", run_item_counts=[1, 1, 1],
        items=[BilledItem(item_id="bill-01", raw_text="DOLO 650", drug_name="Dolo",
                          strength_value=650.0, strength_unit="mg", form="tablet",
                          quantity=10.0, line_total=Decimal("30"), confidence=0.9)],
    )
    return rx, bill


class TestItNeverReadsAFinding:
    """The separation this module exists to keep."""

    def test_the_source_names_no_reconciliation_rule_code(self) -> None:
        """Read the module itself. Only ITEM_COUNT_UNSTABLE names a document."""
        source = inspect.getsource(readability)
        for code in (
            "STRENGTH_MISMATCH", "FORM_MISMATCH", "RX_NOT_BILLED",
            "BILL_NOT_PRESCRIBED", "SCHEDULE_H_UNBACKED", "EXPIRED_ITEM",
            "TEST_NOT_BILLED", "TEST_NOT_PRESCRIBED", "DUPLICATE_BILL",
            "NON_MEDICINE_ITEM", "PANEL_PARTIAL", "QUANTITY_SHORT",
        ):
            assert code not in source, f"{code} must not reach the employee's screen"

    def test_a_readable_pair_with_a_real_discrepancy_reports_read(self) -> None:
        """The case that would be wrong twice over if it leaked.

        Their photograph is fine; the mismatch is not theirs to see.
        """
        rx, bill = clean_pair()
        billed_wrong = bill.model_copy(update={
            "items": [bill.items[0].model_copy(update={"strength_value": 325.0})],
        })
        result = engine.reconcile(rx, billed_wrong, processing_ms=0)
        assert result.verdict == "mismatch", "the fixture must actually differ"
        assert _states(result)["prescription"] == "read"
        assert _states(result)["pharmacy_bill"] == "read"
        for doc in readability_of(result):
            assert doc.message is None or "mismatch" not in doc.message.lower()


class TestPerDocument:
    def test_a_pair_that_read_cleanly_says_so(self) -> None:
        rx, bill = clean_pair()
        result = engine.reconcile(rx, bill, processing_ms=0)
        assert _states(result)["prescription"] == "read"
        assert _states(result)["pharmacy_bill"] == "read"

    def test_a_bill_nothing_came_off_is_unreadable(self) -> None:
        rx, _ = clean_pair()
        result = engine.reconcile(rx, PharmacyBill(currency="INR"), processing_ms=0)
        doc = next(d for d in readability_of(result) if d.slot == "pharmacy_bill")
        assert doc.state == "unreadable"
        assert doc.message is not None and "sharper photo" in doc.message
        assert doc.needs_action

    def test_runs_disagreeing_about_a_page_is_partly_unreadable(self) -> None:
        rx, bill = clean_pair()
        shaky = bill.model_copy(update={
            "run_item_counts": [1, 2, 1], "unstable_lines": ["ZINCOVIT 1'S 180.00"],
        })
        result = engine.reconcile(rx, shaky, processing_ms=0)
        assert _states(result)["pharmacy_bill"] == "partly_unreadable"

    def test_an_unreadable_tests_section_is_reported_to_the_submitter(self) -> None:
        """Only they can re-photograph it."""
        rx, bill = clean_pair()
        ordered_but_unread = rx.model_copy(update={"investigations_present": True})
        result = engine.reconcile(ordered_but_unread, bill, processing_ms=0)
        doc = next(d for d in readability_of(result) if d.slot == "prescription")
        assert doc.state == "partly_unreadable"
        assert doc.message is not None and "tests section" in doc.message


class TestTheLabDocuments:
    def test_a_lab_bill_nobody_uploaded_is_not_a_problem(self) -> None:
        rx, bill = clean_pair()
        result = engine.reconcile(rx, bill, processing_ms=0, submission=Submission())
        doc = next(d for d in readability_of(result) if d.slot == "lab_bill")
        assert doc.state == "not_supplied"
        assert doc.message is None

    def test_a_lab_bill_that_arrived_with_nothing_on_it_is_unreadable(self) -> None:
        """The case that used to fail silently.

        The lab bill's own extraction state is discarded by the merge, so this
        only works because `Submission` now carries it out.
        """
        rx, bill = clean_pair()
        stated = Submission(lab_bill_supplied=True, lab_bill_tests_read=0,
                            lab_bill_warnings=["No line could be read."])
        result = engine.reconcile(rx, bill, processing_ms=0, submission=stated)
        doc = next(d for d in readability_of(result) if d.slot == "lab_bill")
        assert doc.state == "unreadable"
        assert doc.message is not None and "lab bill" in doc.message
        assert doc.detail == ["No line could be read."]

    def test_a_lab_bill_recorded_before_we_measured_says_so(self) -> None:
        """Null is not zero.

        Every claim filed before this field existed carries no count. Reading
        that as "nothing came off it" reported perfectly good lab bills as
        unreadable — which is how a null read as a zero always fails.
        """
        rx, bill = clean_pair()
        older = Submission(lab_bill_supplied=True, lab_bill_tests_read=None)
        result = engine.reconcile(rx, bill, processing_ms=0, submission=older)
        doc = next(d for d in readability_of(result) if d.slot == "lab_bill")
        assert doc.state == "not_assessed"
        assert not doc.needs_action

    def test_a_lab_bill_that_read_is_left_alone(self) -> None:
        rx, bill = clean_pair()
        stated = Submission(lab_bill_supplied=True, lab_bill_tests_read=4)
        result = engine.reconcile(rx, bill, processing_ms=0, submission=stated)
        assert _states(result)["lab_bill"] == "read"

    def test_a_lab_report_is_not_in_the_readability_list_at_all(self) -> None:
        """It was never assessed for legibility, so it is not reported as if it
        had been.

        Nothing is extracted from a lab report — no rule consumes one — so
        listing it in a section about photo quality would invite a submitter to
        re-take a photograph that has nothing wrong with it. It is acknowledged
        among the documents received instead.
        """
        rx, bill = clean_pair()
        stated = Submission(lab_report_supplied=True)
        result = engine.reconcile(rx, bill, processing_ms=0, submission=stated)
        slots: list[str] = [d.slot for d in readability_of(result)]
        assert "lab_report" not in slots


def test_a_record_that_no_longer_validates_says_nothing_rather_than_ok() -> None:
    """An older blob must not open as a clean bill of health."""
    docs = unavailable()
    assert {d.state for d in docs} == {"not_assessed"}
    assert all(d.message and "older format" in d.message for d in docs)


def test_every_document_that_is_read_is_answered_for() -> None:
    """The three the system actually reads. The lab report is not one."""
    rx, bill = clean_pair()
    result = engine.reconcile(rx, bill, processing_ms=0)
    assert [d.slot for d in readability_of(result)] == [
        "prescription", "pharmacy_bill", "lab_bill",
    ]
