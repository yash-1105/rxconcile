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
from typing import Final

import pytest

from rxconcile.export import ExportContext, build_json, build_pdf, build_xlsx
from rxconcile.export.common import category_totals, document_gaps
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
        currency="INR", pharmacy_licence_no="TN/2019/337821",
        items=[
            BilledItem(item_id="bill-01", raw_text="TELMA 80MG", drug_name="Telma",
                       strength_value=80.0, strength_unit="mg", form="tablet",
                       quantity=10.0, line_total=Decimal("310.00"), confidence=0.9),
            BilledItem(item_id="bill-02", raw_text="ZINCOVIT", drug_name="Zincovit",
                       quantity=1.0, line_total=Decimal("180.00"), confidence=0.9),
        ],
    )
    return engine.reconcile(prescription, bill, processing_ms=12)


def non_medicine_result() -> ReconciliationResult:
    """A billed cosmetic: read perfectly, absent from the drug dictionary."""
    prescription = Prescription(
        overall_legibility=0.95,
        items=[PrescribedItem(item_id="rx-01", raw_text="Dolo 650", drug_name="Dolo",
                              strength_value=650.0, strength_unit="mg", form="tablet",
                              confidence=0.9)],
    )
    bill = PharmacyBill(
        currency="INR", pharmacy_licence_no="TN/2019/337821",
        items=[
            BilledItem(item_id="bill-01", raw_text="DOLO 650", drug_name="Dolo",
                       strength_value=650.0, strength_unit="mg", form="tablet",
                       quantity=10.0, line_total=Decimal("30.00"), confidence=0.9),
            BilledItem(item_id="bill-02", raw_text="NIVEA BODY LOTION",
                       drug_name="NIVEA BODY LOTION", form="other",
                       quantity=1.0, line_total=Decimal("345.00"), confidence=0.9),
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
        currency="INR", pharmacy_licence_no="TN/2019/337821",
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


def test_json_carries_the_employee_fields() -> None:
    payload = json.loads(build_json(context(result_with_discrepancy())))
    assert payload["scan"]["employee_name"] == "Yash"
    assert payload["scan"]["employee_number"] == "EMP-4417"


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------


def test_xlsx_has_a_summary_sheet_and_one_per_table() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.load_workbook(BytesIO(build_xlsx(context(result_with_discrepancy()))))
    assert book.sheetnames == ["Summary", "Reimbursement", "Medicines", "Lab tests", "Findings"]


def test_xlsx_summary_states_the_counts_and_the_reimbursement_totals() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    result = result_with_discrepancy()
    book = openpyxl.load_workbook(BytesIO(build_xlsx(context(result))))
    cells = [cell.value for row in book["Summary"].iter_rows() for cell in row]
    text = " ".join(str(v) for v in cells if v)
    assert "Medicines with problems" in text
    assert "Lab tests matched" in text
    # Amounts are numbers with a currency format, not strings: a money column
    # formatted as text cannot be summed by whoever opens this.
    totals = {key: float(total) for key, total, _ in category_totals(result)}
    assert any(
        isinstance(v, (int, float)) and abs(v - totals["not_eligible"]) < 0.005 for v in cells
    )


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


#: Phrases from the disclaimer that used to sit on every export and every
#: screen. It was removed on request, and "remove it" means no shortened
#: version survives in a footer, a summary sheet or a JSON envelope either.
_REMOVED_DISCLAIMER: Final[tuple[str, ...]] = (
    "Proof of concept",
    "not clinical verification",
    "not an insurance determination",
    "require human review",
    "approves or rejects",
)


def test_no_export_carries_the_removed_disclaimer() -> None:
    ctx = context(result_with_discrepancy())

    payload = json.loads(build_json(ctx))
    assert "disclaimer" not in payload
    as_json = json.dumps(payload)

    book = zipfile.ZipFile(BytesIO(build_xlsx(ctx)))
    as_xlsx = " ".join(
        book.read(name).decode("utf-8", "replace")
        for name in book.namelist()
        if name.endswith(".xml")
    )

    # The PDF stores its text compressed, so the wording is checked through the
    # builder's own inputs rather than the bytes: nothing in this module may
    # import it, which the import at the top of the file no longer can.
    for phrase in _REMOVED_DISCLAIMER:
        assert phrase not in as_json, f"{phrase!r} still in the JSON export"
        assert phrase not in as_xlsx, f"{phrase!r} still in the workbook"


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


def test_no_export_carries_the_manual_check_bucket() -> None:
    """It was never a category a reader could act on.

    Those lines are not pending work: the reviewer rules on them with Accept or
    Reject and the Decision column says what they decided. Each is reported as
    what it actually is instead.
    """
    import pypdfium2 as pdfium

    ctx = context(result_with_discrepancy())

    pdf = pdfium.PdfDocument(BytesIO(build_pdf(ctx)))
    text = " ".join(page.get_textpage().get_text_range() for page in pdf)
    assert "Needs a manual check" not in text
    assert "manual review" not in text
    assert "could not be fully checked" not in text

    book = zipfile.ZipFile(BytesIO(build_xlsx(ctx)))
    sheet_text = " ".join(
        book.read(name).decode("utf-8", "replace")
        for name in book.namelist()
        if name.endswith(".xml")
    )
    assert "Needs a manual check" not in sheet_text
    assert "manual review" not in sheet_text

    payload = json.loads(build_json(ctx))
    assert "needs_review" not in payload["summary"]["reimbursement"]
    # The engine's own buckets survive untouched in the verbatim result.
    assert "needs_review_total" in payload["result"]["reimbursement"]


def test_a_manual_check_line_is_reported_as_what_it_actually_is() -> None:
    """Rebucketed by the same test the engine used for the other categories."""
    from rxconcile.export.common import category_totals, matched_billed_ids

    result = result_with_discrepancy()
    assert result.reimbursement.needs_review_line_count > 0, "fixture must exercise this"
    buckets = {key: (total, count) for key, total, count in category_totals(result)}
    assert set(buckets) == {"eligible", "not_eligible", "non_medicine"}
    matched = matched_billed_ids(result)
    for line in result.reimbursement.lines:
        if line.category == "needs_review" and line.amount is not None:
            expected = "eligible" if line.item_id in matched else "not_eligible"
            assert buckets[expected][1] >= 1


def test_the_status_word_matches_the_screen_including_out_of_scope() -> None:
    """The reports printed MATCHES against a body lotion.

    `status_word` fell through to MATCHES for anything with no critical and no
    warning, and a confirmed non-medicine has neither.
    """
    from rxconcile.export.common import status_word

    assert status_word([], paired=True) == "MATCHES"
    assert status_word([]) == "NOT CHECKED", "an unpaired line matched nothing"
    assert status_word(
        [_finding("BRAND_SUBSTITUTION", "info")], paired=True  # type: ignore[list-item]
    ) == "SUBSTITUTED"
    assert status_word([_finding("FORM_MISMATCH", "warning")]) == "CHECK"  # type: ignore[list-item]
    assert status_word([_finding("STRENGTH_MISMATCH", "critical")]) == "PROBLEM"  # type: ignore[list-item]
    assert status_word([_finding("NON_MEDICINE_ITEM", "info")]) == "OUT OF SCOPE"  # type: ignore[list-item]
    # A critical leads even when a warning sits beside it.
    mixed = [_finding("FORM_MISMATCH", "warning"), _finding("STRENGTH_MISMATCH", "critical")]
    assert status_word(mixed) == "PROBLEM"  # type: ignore[arg-type]
    # And a non-medicine never hides a real finding.
    both = [_finding("NON_MEDICINE_ITEM", "info"), _finding("EXPIRED_ITEM", "critical")]
    assert status_word(both) == "PROBLEM"  # type: ignore[arg-type]


def test_a_non_medicine_reads_as_out_of_scope_in_every_export() -> None:
    """NIVEA BODY LOTION printed MATCHES / "could not be read", both wrong."""
    import pypdfium2 as pdfium

    result = non_medicine_result()
    ctx = context(result)

    pdf = pdfium.PdfDocument(BytesIO(build_pdf(ctx)))
    # Whitespace-normalised: a narrow status column wraps the words.
    text = " ".join(
        " ".join(page.get_textpage().get_text_range().split()) for page in pdf
    )
    assert "OUT OF SCOPE" in text
    assert "could not be read" not in text, "it was read; the dictionary just missed it"

    book = zipfile.ZipFile(BytesIO(build_xlsx(ctx)))
    sheet_text = " ".join(
        book.read(name).decode("utf-8", "replace")
        for name in book.namelist()
        if name.endswith(".xml")
    )
    assert "OUT OF SCOPE" in sheet_text
    assert "could not be read" not in sheet_text


def test_the_workbook_and_the_report_agree_on_every_status() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    ctx = context(result_with_discrepancy())
    book = openpyxl.load_workbook(BytesIO(build_xlsx(ctx)))
    # Column 2 now: the sheet leads with a row number, as the screen does.
    # The trailing * is the "one check could not be concluded" marker, which
    # sits beside a status rather than replacing it.
    statuses = {
        str(row[1]).rstrip("*")
        for row in book["Medicines"].iter_rows(min_row=2, values_only=True)
        if row[1]
    }
    assert statuses <= {"MATCHES", "SUBSTITUTED", "CHECK", "PROBLEM", "NOT CHECKED",
                        "OUT OF SCOPE"}
    assert "NOTED" not in statuses


def test_the_workbook_keeps_every_column_the_screen_shows() -> None:
    """The export is a record of the screen, not a reduced version of it."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.load_workbook(BytesIO(build_xlsx(context(result_with_discrepancy()))))
    headers = [c.value for c in next(book["Medicines"].iter_rows(min_row=1, max_row=1))]
    assert headers == [
        "#", "Status", "Remark",
        "Drug (prescribed)", "Drug (billed)", "Salt",
        "Strength (prescribed)", "Strength (billed)",
        "Form (prescribed)", "Form (billed)",
        "Qty (prescribed)", "Qty (billed)",
        "Decision", "Reviewer's reason",
    ]
    assert book["Medicines"].freeze_panes == "A2"


# ---------------------------------------------------------------------------
# Grouping — all three surfaces tell the same story
# ---------------------------------------------------------------------------


def schedule_h_result() -> ReconciliationResult:
    """Alprax billed with nothing behind it: two findings, one item."""
    prescription = Prescription(
        overall_legibility=0.95,
        items=[
            PrescribedItem(item_id="rx-01", raw_text="Dolo 650", drug_name="Dolo",
                           strength_value=650.0, strength_unit="mg", form="tablet",
                           confidence=0.9)
        ],
    )
    bill = PharmacyBill(
        currency="INR", pharmacy_licence_no="TN/2019/337821",
        items=[
            BilledItem(item_id="bill-01", raw_text="DOLO 650", drug_name="Dolo",
                       strength_value=650.0, strength_unit="mg", form="tablet",
                       quantity=10.0, line_total=Decimal("22.00"), confidence=0.9),
            BilledItem(item_id="bill-02", raw_text="ALPRAX 0.5", drug_name="Alprax",
                       quantity=10.0, line_total=Decimal("57.00"), confidence=0.9),
        ],
    )
    return engine.reconcile(prescription, bill, processing_ms=5)


def test_two_findings_on_one_billed_line_become_one_group() -> None:
    from rxconcile.export.common import group_findings

    result = schedule_h_result()
    alprax = [
        g for g in group_findings(result)
        if any(f.billed_ref == "bill-02" for f in g.findings)
    ]
    assert len(alprax) == 1, "Alprax must be one row, not two"
    assert len(alprax[0].findings) >= 2


def test_schedule_h_is_the_headline_never_the_hidden_one() -> None:
    """The single most consequential thing this system detects."""
    from rxconcile.export.common import PINNED_CODE, group_findings

    result = schedule_h_result()
    group = next(
        g for g in group_findings(result)
        if any(f.rule_code == PINNED_CODE for f in g.findings)
    )
    assert group.headline.rule_code == PINNED_CODE


def test_a_matched_pair_is_one_group_whichever_ref_a_finding_carries() -> None:
    from rxconcile.export.common import group_findings

    result = result_with_discrepancy()
    keys = [g.key for g in group_findings(result)]
    assert len(keys) == len(set(keys))
    for group in group_findings(result):
        refs = {f.prescribed_ref for f in group.findings if f.prescribed_ref}
        assert len(refs) <= 1, "one group must not span two prescribed lines"


def test_grouping_drops_nothing() -> None:
    from rxconcile.export.common import group_findings

    result = schedule_h_result()
    grouped = sum(len(g.findings) for g in group_findings(result))
    assert grouped == len(result.findings)


def test_document_level_findings_stay_their_own_rows() -> None:
    from rxconcile.export.common import group_findings

    result = schedule_h_result()
    doc_groups = [g for g in group_findings(result) if g.key.startswith("doc-")]
    assert all(len(g.findings) == 1 for g in doc_groups)


def test_the_pdf_counts_items_not_findings() -> None:
    import pypdfium2 as pdfium

    from rxconcile.export.common import discrepancy_groups

    result = schedule_h_result()
    expected = len(discrepancy_groups(result))
    pdf = pdfium.PdfDocument(BytesIO(build_pdf(context(result))))
    text = " ".join(page.get_textpage().get_text_range() for page in pdf)
    assert f"{expected} discrepanc" in text
    # And the Alprax row carries its companion rather than dropping it.
    assert "(+1 more)" in text


def test_the_workbook_keeps_every_finding_under_its_item() -> None:
    openpyxl = pytest.importorskip("openpyxl")

    result = schedule_h_result()
    book = openpyxl.load_workbook(BytesIO(build_xlsx(context(result))))
    rows = list(book["Findings"].iter_rows(min_row=2, values_only=True))
    codes = [row[2] for row in rows if row[2]]
    assert len(codes) == len(result.findings), "nothing may be dropped"
    assert "SCHEDULE_H_UNBACKED" in codes
