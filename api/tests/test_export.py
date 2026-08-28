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


# ---------------------------------------------------------------------------
# Remark capping
# ---------------------------------------------------------------------------


def _finding(rule_code: str, severity: str, **detail: object) -> object:
    from rxconcile.models import Finding

    return Finding(
        rule_code=rule_code, severity=severity, message="ignored",  # type: ignore[arg-type]
        prescribed_ref=None, billed_ref=None, detail=detail,
    )


def test_a_remark_is_one_sentence_naming_the_most_severe_thing() -> None:
    from rxconcile.export.common import short_remark

    found = [
        _finding("QUANTITY_AMBIGUOUS", "info"),
        _finding("BRAND_SUBSTITUTION", "info",
                 prescribed_brand="Dolo", billed_brand="Calpol"),
        _finding("STRENGTH_MISMATCH", "critical",
                 expected={"value": 625, "unit": "mg"}, found={"value": 375, "unit": "mg"}),
    ]
    remark = short_remark(found)  # type: ignore[arg-type]
    assert remark == "Strength differs: 625mg vs 375mg (+2 more)"
    assert "\n" not in remark
    assert remark.count(";") == 0, "statements must not be stacked into one cell"


def test_a_single_finding_gets_no_count() -> None:
    from rxconcile.export.common import short_remark

    found = [_finding("BRAND_SUBSTITUTION", "info",
                      prescribed_brand="Dolo", billed_brand="Calpol")]
    assert short_remark(found) == "Brand substitution — Calpol for Dolo, same salt"  # type: ignore[arg-type]


def test_a_row_with_nothing_against_it_has_no_remark() -> None:
    from rxconcile.export.common import short_remark

    assert short_remark([]) == "—"


def test_the_unchecked_line_explains_the_needs_review_total() -> None:
    """An unexplained money figure on a report is worse than a technical one."""
    from rxconcile.export.common import unchecked_line

    result = result_with_discrepancy()
    line = unchecked_line(result)
    assert line is not None
    assert str(result.reimbursement.needs_review_line_count) in line
    assert "manual review" in line
    assert "shown in the table above" in line


def test_no_unchecked_line_when_nothing_needs_review() -> None:
    from rxconcile.export.common import unchecked_line

    clean = engine.reconcile(
        Prescription(overall_legibility=0.9), PharmacyBill(currency="INR"), processing_ms=1
    )
    assert unchecked_line(clean) is None


def test_the_pdf_no_longer_lists_internal_check_names() -> None:
    """The check-name table meant nothing to a client and is gone."""
    import pypdfium2 as pdfium

    data = build_pdf(context(result_with_discrepancy()))
    pdf = pdfium.PdfDocument(BytesIO(data))
    text = " ".join(page.get_textpage().get_text_range() for page in pdf)
    assert "Checks that could not run" not in text
    assert "manual review" in text, "the total still has to be explained"


def test_the_status_word_is_the_same_everywhere() -> None:
    """The workbook printed NOTED where the report and screen said MATCHES."""
    from rxconcile.export.common import status_word

    assert status_word([]) == "MATCHES"
    assert status_word([_finding("BRAND_SUBSTITUTION", "info")]) == "MATCHES"  # type: ignore[list-item]
    assert status_word([_finding("FORM_MISMATCH", "warning")]) == "CHECK"  # type: ignore[list-item]
    assert status_word([_finding("STRENGTH_MISMATCH", "critical")]) == "PROBLEM"  # type: ignore[list-item]
    # A critical leads even when a warning sits beside it.
    mixed = [_finding("FORM_MISMATCH", "warning"), _finding("STRENGTH_MISMATCH", "critical")]
    assert status_word(mixed) == "PROBLEM"  # type: ignore[arg-type]


def test_the_workbook_and_the_report_agree_on_every_status() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    ctx = context(result_with_discrepancy())
    book = openpyxl.load_workbook(BytesIO(build_xlsx(ctx)))
    statuses = {
        row[0] for row in book["Medicines"].iter_rows(min_row=2, values_only=True) if row[0]
    }
    assert statuses <= {"MATCHES", "CHECK", "PROBLEM"}
    assert "NOTED" not in statuses
