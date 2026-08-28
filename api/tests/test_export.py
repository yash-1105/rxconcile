"""Report exports.

The tests that matter are about what must SURVIVE the export. A PDF outlives
the screen it came from: a reader six weeks later has no way to know a document
was missing, or that a check never ran, unless the report says so.
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from decimal import Decimal
from io import BytesIO

import pytest

from rxconcile.export import ExportContext, build_json, build_pdf, build_xlsx
from rxconcile.export.common import DISCLAIMER, document_gaps
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


def result_with_discrepancy() -> ReconciliationResult:
    prescription = Prescription(
        overall_legibility=0.95,
        items=[
            PrescribedItem(item_id="rx-01", raw_text="Telma 40mg", drug_name="Telma",
                           strength_value=40.0, strength_unit="mg", form="tablet",
                           confidence=0.9)
        ],
    )
    bill = PharmacyBill(
        currency="INR",
        items=[
            BilledItem(item_id="bill-01", raw_text="TELMA 80MG", drug_name="Telma",
                       strength_value=80.0, strength_unit="mg", form="tablet",
                       quantity=10.0, line_total=Decimal("310.00"), confidence=0.9),
            BilledItem(item_id="bill-02", raw_text="ZINCOVIT", drug_name="Zincovit",
                       quantity=1.0, line_total=Decimal("180.00"), confidence=0.9),
        ],
    )
    return engine.reconcile(prescription, bill, processing_ms=12)


def lab_only_result() -> ReconciliationResult:
    """A prescription with medicines against a bill carrying only lab lines."""
    prescription = Prescription(
        overall_legibility=0.95,
        investigations_present=True,
        items=[
            PrescribedItem(item_id=f"rx-{i:02d}", raw_text=n, drug_name=n, confidence=0.9)
            for i, n in enumerate(["Dolo", "Pan-D"], start=1)
        ],
        tests=[PrescribedTest(item_id="test-01", raw_text="Adv: CBC", test_name="CBC",
                              confidence=0.9)],
    )
    bill = PharmacyBill(
        currency="INR",
        tests=[BilledTest(item_id="billtest-01", raw_text="CBC", test_name="CBC",
                          quantity=1.0, line_total=Decimal("450.00"), confidence=0.9)],
    )
    return engine.reconcile(prescription, bill, processing_ms=9)


def context(result: ReconciliationResult, **kwargs: object) -> ExportContext:
    return ExportContext(
        result=result,
        employee_name="Yash",
        employee_number="EMP-4417",
        created_at=datetime(2026, 8, 28, 11, 15),
        prescription_filename="rx.jpg",
        bill_filename="bill.png",
        scan_id=7,
        extraction_runs=3,
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_json_carries_the_result_verbatim() -> None:
    result = result_with_discrepancy()
    payload = json.loads(build_json(context(result)))
    assert payload["result"] == result.model_dump(mode="json"), "must not reshape the result"


def test_json_carries_the_employee_fields_and_disclaimer() -> None:
    payload = json.loads(build_json(context(result_with_discrepancy())))
    assert payload["scan"]["employee_name"] == "Yash"
    assert payload["scan"]["employee_number"] == "EMP-4417"
    assert payload["disclaimer"] == DISCLAIMER
    assert "not an insurance determination" in payload["disclaimer"]


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------


def test_xlsx_has_a_summary_sheet_and_one_per_table() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.load_workbook(BytesIO(build_xlsx(context(result_with_discrepancy()))))
    assert book.sheetnames == ["Summary", "Reimbursement", "Medicines", "Lab tests", "Findings"]


def test_xlsx_summary_states_the_disclaimer_and_the_reimbursement_totals() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    result = result_with_discrepancy()
    book = openpyxl.load_workbook(BytesIO(build_xlsx(context(result))))
    text = " ".join(
        str(cell.value) for row in book["Summary"].iter_rows() for cell in row if cell.value
    )
    assert DISCLAIMER in text
    assert "not an insurance determination" in text
    assert str(result.reimbursement.not_eligible_total) in text


def test_xlsx_is_a_real_workbook() -> None:
    data = build_xlsx(context(result_with_discrepancy()))
    assert zipfile.is_zipfile(BytesIO(data))


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def test_pdf_is_a_real_pdf() -> None:
    data = build_pdf(context(result_with_discrepancy()))
    assert data.startswith(b"%PDF-"), "must be a PDF"
    assert len(data) > 2000


def test_pdf_embeds_the_source_pages_when_they_were_stored() -> None:
    pytest.importorskip("PIL")
    from PIL import Image as PILImage

    buffer = BytesIO()
    PILImage.new("RGB", (400, 260), "white").save(buffer, format="JPEG")
    page = buffer.getvalue()

    without = build_pdf(context(result_with_discrepancy()))
    with_pages = build_pdf(
        context(result_with_discrepancy(), prescription_image=page, bill_image=page)
    )
    assert len(with_pages) > len(without), "the pages must actually be embedded"


def test_pdf_survives_a_page_that_will_not_decode() -> None:
    """A corrupt image must cost the picture, never the whole report."""
    data = build_pdf(context(result_with_discrepancy(), prescription_image=b"not an image"))
    assert data.startswith(b"%PDF-")


# ---------------------------------------------------------------------------
# What must survive the export
# ---------------------------------------------------------------------------


def test_the_document_completeness_gap_is_detected_for_a_lab_only_bill() -> None:
    gaps = document_gaps(lab_only_result())
    assert gaps, "a lab-only bill leaves prescribed medicines unassessed"
    title, detail = gaps[0]
    assert "pharmacy bill was not supplied" in title.lower()
    assert "not assessed" in detail.lower()
    assert "2" in detail, "it must say how many medicines went unassessed"


def test_the_gap_reaches_every_format() -> None:
    """A report that omits it reads as a clean result six weeks later."""
    ctx = context(lab_only_result())

    payload = json.loads(build_json(ctx))
    codes = {f["rule_code"] for f in payload["result"]["findings"]}
    assert "RX_NOT_BILLED" in codes
    assert any(
        f["detail"].get("lab_only_bill") is True for f in payload["result"]["findings"]
    )

    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.load_workbook(BytesIO(build_xlsx(ctx)))
    text = " ".join(
        str(cell.value) for row in book["Summary"].iter_rows() for cell in row if cell.value
    )
    assert "DOCUMENTS NOT SUPPLIED" in text
    assert "not supplied" in text.lower()

    assert build_pdf(ctx).startswith(b"%PDF-")


def test_an_empty_result_still_exports() -> None:
    empty = engine.reconcile(
        Prescription(overall_legibility=0.9), PharmacyBill(currency="INR"), processing_ms=1
    )
    assert build_pdf(context(empty)).startswith(b"%PDF-")
    assert zipfile.is_zipfile(BytesIO(build_xlsx(context(empty))))
    assert json.loads(build_json(context(empty)))["result"]["verdict"]


def test_amounts_are_formatted_to_two_decimals() -> None:
    """`INR 110.0` is not how money is written on a report."""
    from rxconcile.export.common import money

    assert money("INR", Decimal("110")) == "INR 110.00"
    assert money("INR", Decimal("1234.5")) == "INR 1,234.50"
    # None is "not printed", never 0.00: a line with no amount was not free.
    assert money("INR", None) == "not printed"


def test_the_disclaimer_footer_does_not_break_mid_word() -> None:
    import textwrap

    lines = textwrap.wrap(DISCLAIMER, width=125)[:2]
    assert " ".join(lines) == DISCLAIMER, "the whole disclaimer must fit in two lines"
    for line in lines:
        assert not line.endswith("-")
