"""Excel export: a summary sheet plus one sheet per table."""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from rxconcile.export.common import (
    CATEGORY_LABEL,
    DISCLAIMER,
    STATUS_WORD,
    ExportContext,
    canonical_by_id,
    decision_remark,
    decision_word,
    discrepancy_groups,
    document_gaps,
    group_findings,
    money,
    row_key,
    short_remark,
    status_word,
    unchecked_line,
)

_HEAD = Font(bold=True, color="FFFFFF")
_HEAD_FILL = PatternFill("solid", fgColor="0E4F45")
_TITLE = Font(bold=True, size=14)
_WRAP = Alignment(wrap_text=True, vertical="top")


def _headers(sheet: Worksheet, row: int, labels: list[str]) -> None:
    for column, label in enumerate(labels, start=1):
        cell = sheet.cell(row=row, column=column, value=label)
        cell.font = _HEAD
        cell.fill = _HEAD_FILL


def _widths(sheet: Worksheet, widths: list[int]) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _summary_sheet(sheet: Worksheet, context: ExportContext) -> None:
    result = context.result
    sheet.title = "Summary"
    _widths(sheet, [34, 60])
    sheet["A1"] = "rxconcile reconciliation report"
    sheet["A1"].font = _TITLE

    row = 3
    for label, value in (
        ("Employee", context.employee_name),
        ("Employee number", context.employee_number),
        ("Date", context.when),
        ("Prescription", context.prescription_filename),
        ("Bill", context.bill_filename),
        ("Verdict", result.verdict),
        ("Extraction runs", context.extraction_runs or ""),
        ("Items needing attention", len(discrepancy_groups(result))),
        ("Lines needing a manual check", result.reimbursement.needs_review_line_count),
    ):
        sheet.cell(row=row, column=1, value=label).font = Font(bold=True)
        sheet.cell(row=row, column=2, value=value)
        row += 1

    gaps = document_gaps(result)
    if gaps:
        row += 1
        sheet.cell(row=row, column=1, value="DOCUMENTS NOT SUPPLIED").font = Font(bold=True)
        row += 1
        for title, detail in gaps:
            sheet.cell(row=row, column=1, value=title).font = Font(bold=True)
            cell = sheet.cell(row=row, column=2, value=detail)
            cell.alignment = _WRAP
            row += 1

    row += 1
    purse = result.reimbursement
    sheet.cell(row=row, column=1, value="REIMBURSEMENT").font = Font(bold=True)
    row += 2
    for label, total, count in (
        (CATEGORY_LABEL["eligible"], purse.eligible_total, purse.eligible_line_count),
        (CATEGORY_LABEL["not_eligible"], purse.not_eligible_total,
         purse.not_eligible_line_count),
        (CATEGORY_LABEL["needs_review"], purse.needs_review_total,
         purse.needs_review_line_count),
        (CATEGORY_LABEL["non_medicine"], purse.non_medicine_total,
         purse.non_medicine_line_count),
    ):
        sheet.cell(row=row, column=1, value=label).font = Font(bold=True)
        sheet.cell(row=row, column=2, value=f"{money(purse.currency, total)} ({count} lines)")
        row += 1
    if purse.lines_without_amount:
        sheet.cell(row=row, column=1, value="Lines with no printed amount").font = Font(bold=True)
        sheet.cell(
            row=row, column=2,
            value=f"{purse.lines_without_amount} — excluded from the totals above, "
                  "not counted as zero",
        )
        row += 1

    row += 1
    sheet.cell(row=row, column=1, value="Disclaimer").font = Font(bold=True)
    cell = sheet.cell(row=row, column=2, value=DISCLAIMER)
    cell.alignment = _WRAP


def _findings_sheet(sheet: Worksheet, context: ExportContext) -> None:
    """One row per item, matching the screen and the report.

    Grouped rows are ordered headline-first and marked, so a reader can see
    that two lines belong to one medicine without losing either of them.
    """
    sheet.title = "Findings"
    _widths(sheet, [8, 12, 24, 66, 16, 16])
    _headers(sheet, 1, ["Item", "Status", "Rule", "What it says", "Prescribed ref", "Billed ref"])
    row = 2
    for index, group in enumerate(group_findings(context.result), start=1):
        for position, finding in enumerate(group.findings):
            sheet.cell(row=row, column=1, value=index if position == 0 else "")
            sheet.cell(
                row=row, column=2,
                value=STATUS_WORD.get(finding.severity, finding.severity)
                if position == 0
                else "",
            )
            sheet.cell(row=row, column=3, value=finding.rule_code)
            message = finding.message
            if position == 0 and group.extra:
                message += f" (+{group.extra} more)"
            sheet.cell(row=row, column=4, value=message).alignment = _WRAP
            sheet.cell(row=row, column=5, value=finding.prescribed_ref or "—")
            sheet.cell(row=row, column=6, value=finding.billed_ref or "—")
            row += 1


def _medicines_sheet(sheet: Worksheet, context: ExportContext) -> None:
    result = context.result
    sheet.title = "Medicines"
    _widths(sheet, [10, 18, 18, 30, 14, 14, 12, 12, 12, 12, 40, 13, 34])
    _headers(sheet, 1, [
        "Status", "Drug (prescribed)", "Drug (billed)", "Salt",
        "Strength (prescribed)", "Strength (billed)",
        "Form (prescribed)", "Form (billed)",
        "Qty (prescribed)", "Qty (billed)", "Remark",
        "Decision", "Reviewer's reason",
    ])
    canonical = canonical_by_id(result)
    rx = {item.item_id: item for item in result.prescription.items}
    bill = {item.item_id: item for item in result.bill.items}

    def strength(item: object) -> str:
        value = getattr(item, "strength_value", None)
        if value is None:
            return "—"
        return f"{value}{getattr(item, 'strength_unit', '') or ''}"

    rows: list[tuple[str | None, str | None]] = [
        (pair.prescribed_id, pair.billed_id) for pair in result.matched_pairs
    ]
    rows += [(item_id, None) for item_id in result.unmatched_prescribed]
    rows += [(None, item_id) for item_id in result.unmatched_billed]

    for row, (rx_id, bill_id) in enumerate(rows, start=2):
        found = [
            f for f in result.findings
            if (rx_id and f.prescribed_ref == rx_id) or (bill_id and f.billed_ref == bill_id)
        ]
        status = status_word(found)
        rx_item = rx.get(rx_id or "")
        bill_item = bill.get(bill_id or "")
        rx_match, bill_match = canonical.get(rx_id or ""), canonical.get(bill_id or "")
        salt = (rx_match.salt if rx_match else None) or (bill_match.salt if bill_match else None)

        sheet.cell(row=row, column=1, value=status)
        sheet.cell(row=row, column=2, value=getattr(rx_item, "drug_name", None) or "—")
        sheet.cell(row=row, column=3, value=getattr(bill_item, "drug_name", None) or "—")
        sheet.cell(row=row, column=4, value=salt or "—")
        sheet.cell(row=row, column=5, value=strength(rx_item) if rx_item else "—")
        sheet.cell(row=row, column=6, value=strength(bill_item) if bill_item else "—")
        sheet.cell(row=row, column=7, value=getattr(rx_item, "form", None) or "—")
        sheet.cell(row=row, column=8, value=getattr(bill_item, "form", None) or "—")
        expected = next(
            (f.detail.get("expected_units") for f in found if "expected_units" in f.detail), None
        )
        sheet.cell(row=row, column=9, value=expected if expected is not None else "—")
        quantity = getattr(bill_item, "quantity", None)
        sheet.cell(row=row, column=10, value=quantity if quantity is not None else "—")
        cell = sheet.cell(row=row, column=11, value=short_remark(found))
        cell.alignment = _WRAP
        key = row_key(rx_id, bill_id)
        sheet.cell(row=row, column=12, value=decision_word(context.decisions, key))
        sheet.cell(
            row=row, column=13, value=decision_remark(context.decisions, key) or "—"
        ).alignment = _WRAP


def _tests_sheet(sheet: Worksheet, context: ExportContext) -> None:
    result = context.result
    sheet.title = "Lab tests"
    _widths(sheet, [10, 26, 26, 26, 50, 13, 34])
    _headers(sheet, 1, [
        "Status", "Test (prescribed)", "Test (billed)", "Panel", "Remark",
        "Decision", "Reviewer's reason",
    ])

    if not result.prescription.tests and not result.bill.tests:
        present = result.prescription.investigations_present
        sheet.cell(row=2, column=1, value="—")
        sheet.cell(row=2, column=5, value=(
            "No investigations ordered on this prescription."
            if present is False
            else "An investigations section is present but could not be read. This is NOT "
                 "a finding that no tests were ordered."
            if present
            else "No investigations section was found, but its presence could not be confirmed."
        )).alignment = _WRAP
        return

    rx = {t.item_id: t for t in result.prescription.tests}
    bill = {t.item_id: t for t in result.bill.tests}
    # The flag marks a line billed under an ordered panel. The workbook used to
    # omit these rows entirely, so an ordered CBC billed as six analytes showed
    # one line and dropped five -- the sheet did not account for the whole bill.
    rows: list[tuple[str | None, str | None, bool]] = [
        (pair.prescribed_id, pair.billed_id, False) for pair in result.matched_tests
    ]
    rows += [(item_id, None, False) for item_id in result.unmatched_prescribed_tests]
    rows += [(None, item_id, False) for item_id in result.unmatched_billed_tests]
    accounted = {pair.billed_id for pair in result.matched_tests} | set(
        result.unmatched_billed_tests
    )
    rows += [(None, t.item_id, True) for t in result.bill.tests if t.item_id not in accounted]

    for row, (rx_id, bill_id, covered) in enumerate(rows, start=2):
        found = [
            f for f in result.findings
            if (rx_id and f.prescribed_ref == rx_id) or (bill_id and f.billed_ref == bill_id)
        ]
        worst = status_word(found)
        panel = next(
            (str(f.detail.get("panel") or f.detail.get("resolved_as"))
             for f in found if f.detail.get("panel") or f.detail.get("resolved_as")),
            None,
        )
        sheet.cell(row=row, column=1, value=worst)
        sheet.cell(row=row, column=2, value=getattr(rx.get(rx_id or ""), "test_name", None) or "—")
        billed_name = getattr(bill.get(bill_id or ""), "test_name", None) or "—"
        sheet.cell(row=row, column=3, value=billed_name)
        sheet.cell(row=row, column=4, value=panel or "—")
        remark = short_remark(found)
        if covered and not found:
            remark = "Billed as part of an ordered panel"
        cell = sheet.cell(row=row, column=5, value=remark)
        cell.alignment = _WRAP
        key = row_key(rx_id, bill_id, tests=True, covered=covered)
        sheet.cell(row=row, column=6, value=decision_word(context.decisions, key))
        sheet.cell(
            row=row, column=7, value=decision_remark(context.decisions, key) or "—"
        ).alignment = _WRAP


def _reimbursement_sheet(sheet: Worksheet, context: ExportContext) -> None:
    sheet.title = "Reimbursement"
    _widths(sheet, [16, 30, 14, 30, 55])
    purse = context.result.reimbursement
    offset = 0
    if context.claimed_amount is not None:
        sheet.cell(row=1, column=1, value="Accepted for this claim").font = Font(bold=True)
        sheet.cell(row=1, column=3, value=float(context.claimed_amount))
        sheet.cell(row=2, column=1, value=(
            "The total of the lines a reviewer accepted, from the Decision column on the "
            "Medicines and Lab tests sheets. Lines not on the prescription and lines that "
            "are not medicines are never part of it."
        )).alignment = _WRAP
        offset = 3
    _headers(sheet, 1 + offset, ["Category", "Item", "Amount", "Line", "Why"])
    for row, line in enumerate(purse.lines, start=2 + offset):
        sheet.cell(row=row, column=1, value=CATEGORY_LABEL[line.category])
        sheet.cell(row=row, column=2, value=line.description)
        sheet.cell(
            row=row, column=3,
            value=float(line.amount) if line.amount is not None else "not printed",
        )
        sheet.cell(row=row, column=4, value=line.item_id)
        sheet.cell(row=row, column=5, value=line.reason).alignment = _WRAP
    explanation = unchecked_line(context.result)
    if explanation:
        note = sheet.cell(row=len(purse.lines) + 3 + offset, column=1, value=explanation)
        note.alignment = _WRAP


def build_xlsx(context: ExportContext) -> bytes:
    workbook = Workbook()
    _summary_sheet(workbook.active, context)  # type: ignore[arg-type]
    _reimbursement_sheet(workbook.create_sheet(), context)
    _medicines_sheet(workbook.create_sheet(), context)
    _tests_sheet(workbook.create_sheet(), context)
    _findings_sheet(workbook.create_sheet(), context)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
