"""The transcription a submitter is shown of their own documents.

Two things are load-bearing. It must carry what was READ off each page, so a
submitter can spot a misread strength or a line we missed. And it must carry no
part of the COMPARISON, which is a reviewer's work — the shape is what enforces
that, and these assert the shape.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import get_args

from pydantic import BaseModel

from rxconcile.models import (
    BilledItem,
    BilledTest,
    PharmacyBill,
    PrescribedItem,
    PrescribedTest,
    Prescription,
    Submission,
)
from rxconcile.reconcile import engine
from rxconcile.reconcile.extracted import (
    ExtractedContent,
    billed_total,
    extracted_content,
)


def pair() -> tuple[Prescription, PharmacyBill]:
    rx = Prescription(
        overall_legibility=0.95, investigations_present=True,
        patient_name="Yash Singh", patient_age="41", patient_sex="M",
        prescriber_name="Dr A Mehta", clinic_name="Medicare Polyclinic",
        date_issued=date(2026, 8, 19),
        items=[
            PrescribedItem(item_id="rx-01", raw_text="Tab. Telma 40mg 1-0-0 x 30 days",
                           drug_name="Telma", strength_value=40.0, strength_unit="mg",
                           form="tablet", frequency_raw="1-0-0",
                           duration_raw="x 30 days", confidence=0.9),
        ],
        tests=[PrescribedTest(item_id="t-01", raw_text="Adv: HbA1c",
                              test_name="HbA1c", confidence=0.9)],
    )
    bill = PharmacyBill(
        currency="INR", pharmacy_name="Apollo Pharmacy", bill_no="A-118",
        bill_date=date(2026, 8, 20),
        subtotal=Decimal("300.00"), tax_total=Decimal("15.00"),
        grand_total=Decimal("315.00"),
        items=[
            BilledItem(item_id="bill-01", raw_text="TELMA 40MG", drug_name="Telma",
                       strength_value=40.0, strength_unit="mg", form="tablet",
                       quantity=30.0, pack_size="15'S", unit_price=Decimal("10.00"),
                       line_total=Decimal("300.00"), batch_no="T9912",
                       expiry=date(2027, 6, 30), confidence=0.9),
        ],
    )
    return rx, bill


class TestTheComparisonIsNotInIt:
    def test_no_content_model_has_anywhere_to_put_a_comparison(self) -> None:
        """The boundary is the SHAPE, so the shape is what is asserted.

        Every field of every model a submitter receives, walked recursively. A
        field that could carry a verdict, a pairing or a matched status cannot
        be added without failing here.
        """
        seen: set[str] = set()

        def walk(model: type[BaseModel]) -> None:
            if model in visited:
                return
            visited.add(model)
            for name, field in model.model_fields.items():
                seen.add(name)
                for arg in (field.annotation, *get_args(field.annotation)):
                    for inner in (arg, *get_args(arg)):
                        if isinstance(inner, type) and issubclass(inner, BaseModel):
                            walk(inner)

        visited: set[type] = set()
        walk(ExtractedContent)
        forbidden = {
            "findings", "matched", "unmatched", "matched_pairs", "matched_tests",
            "reimbursement", "rule_code", "severity", "verdict", "score",
            "eligible", "claimable", "supported", "status", "state",
            "discrepancy_count", "prescribed_ref", "billed_ref",
        }
        leaked = seen & forbidden
        assert not leaked, f"a comparison field reached the submitter: {leaked}"
        # And what it DOES carry is only transcription.
        assert "medicines" in seen and "lines" in seen and "billed_total" in seen

    def test_a_bill_line_carries_no_matched_status(self) -> None:
        rx, bill = pair()
        unprescribed = bill.model_copy(update={"items": [
            *bill.items,
            BilledItem(item_id="bill-02", raw_text="ZINCOVIT", drug_name="Zincovit",
                       quantity=15.0, line_total=Decimal("180.00"), confidence=0.9),
        ]})
        content = extracted_content(engine.reconcile(rx, unprescribed, processing_ms=0))
        shapes = {tuple(sorted(line.model_dump())) for line in content.pharmacy_bill.lines}
        assert len(shapes) == 1, "a line nobody prescribed looks like any other"


class TestWhatWasRead:
    def test_the_prescription_comes_back_as_written(self) -> None:
        rx, bill = pair()
        content = extracted_content(engine.reconcile(rx, bill, processing_ms=0))
        page = content.prescription
        assert page.prescriber == "Dr A Mehta"
        assert page.clinic == "Medicare Polyclinic"
        assert page.date == "2026-08-19"
        assert page.patient_name == "Yash Singh"
        assert page.investigations == ["HbA1c"]
        line = page.medicines[0]
        assert (line.name, line.strength, line.form) == ("Telma", "40.0mg", "tablet")
        assert (line.frequency, line.duration) == ("1-0-0", "x 30 days")

    def test_the_bill_comes_back_as_printed(self) -> None:
        rx, bill = pair()
        content = extracted_content(engine.reconcile(rx, bill, processing_ms=0))
        page = content.pharmacy_bill
        assert (page.name, page.bill_no, page.bill_date) == (
            "Apollo Pharmacy", "A-118", "2026-08-20",
        )
        assert (page.subtotal, page.tax, page.grand_total) == ("300.00", "15.00", "315.00")
        line = page.lines[0]
        assert (line.item, line.batch, line.pack) == ("Telma", "T9912", "15'S")
        assert (line.quantity, line.rate, line.amount) == ("30.0", "10.00", "300.00")
        assert line.expiry == "2027-06-30"

    def test_an_amount_that_was_not_printed_stays_null(self) -> None:
        """Never rendered as 0.00: a line with no amount was not free."""
        rx, bill = pair()
        no_amount = bill.model_copy(update={"items": [
            bill.items[0].model_copy(update={"line_total": None, "unit_price": None}),
        ]})
        content = extracted_content(engine.reconcile(rx, no_amount, processing_ms=0))
        assert content.pharmacy_bill.lines[0].amount is None
        assert content.pharmacy_bill.lines[0].rate is None


class TestTheLabBill:
    def test_it_is_shown_as_its_own_document(self) -> None:
        rx, bill = pair()
        lab = PharmacyBill(
            currency="INR", pharmacy_name="Dr Lal PathLabs", bill_no="L-77",
            bill_date=date(2026, 8, 21), grand_total=Decimal("600.00"),
            tests=[BilledTest(item_id="lt-01", raw_text="HbA1c 600.00",
                              test_name="HbA1c", line_total=Decimal("600.00"),
                              confidence=0.9)],
        )
        merged = bill.model_copy(update={"tests": [
            lab.tests[0].model_copy(update={"item_id": "billtest-01"}),
        ]})
        stated = Submission(lab_bill_supplied=True, lab_bill=lab,
                            lab_bill_merged_ids=["billtest-01"])
        content = extracted_content(
            engine.reconcile(rx, merged, processing_ms=0, submission=stated)
        )
        assert content.lab_bill is not None
        assert content.lab_bill.name == "Dr Lal PathLabs"
        assert content.lab_bill.tests[0].test == "HbA1c"
        # And NOT a second time under the pharmacy bill.
        assert all(line.item != "HbA1c" for line in content.pharmacy_bill.lines)

    def test_no_lab_bill_means_no_section(self) -> None:
        rx, bill = pair()
        content = extracted_content(engine.reconcile(rx, bill, processing_ms=0))
        assert content.lab_bill is None


class TestTheTotal:
    def test_it_is_the_documents_own_printed_total(self) -> None:
        rx, bill = pair()
        assert billed_total(engine.reconcile(rx, bill, processing_ms=0)) == "315.00"

    def test_both_bills_are_added_when_both_were_uploaded(self) -> None:
        rx, bill = pair()
        lab = PharmacyBill(currency="INR", grand_total=Decimal("600.00"),
                           tests=[BilledTest(item_id="lt-01", raw_text="HbA1c",
                                             test_name="HbA1c",
                                             line_total=Decimal("600.00"),
                                             confidence=0.9)])
        stated = Submission(lab_bill_supplied=True, lab_bill=lab,
                            lab_bill_merged_ids=[])
        result = engine.reconcile(rx, bill, processing_ms=0, submission=stated)
        assert billed_total(result) == "915.00"

    def test_it_is_summed_only_when_every_line_printed_an_amount(self) -> None:
        """A total quietly missing a line is worse than no total."""
        rx, bill = pair()
        short = bill.model_copy(update={
            "grand_total": None, "subtotal": None,
            "items": [
                bill.items[0],
                BilledItem(item_id="bill-02", raw_text="ZINCOVIT", drug_name="Zincovit",
                           quantity=1.0, line_total=None, confidence=0.9),
            ],
        })
        assert billed_total(engine.reconcile(rx, short, processing_ms=0)) is None

    def test_it_falls_back_to_summing_a_bill_that_prints_no_total(self) -> None:
        rx, bill = pair()
        untotalled = bill.model_copy(update={"grand_total": None, "subtotal": None})
        assert billed_total(engine.reconcile(rx, untotalled, processing_ms=0)) == "300.00"
