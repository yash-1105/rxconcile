"""PDF report.

Two constraints shape this file.

**It must read in greyscale.** A printed report loses colour, so no status is
ever carried by colour alone: every row states its status as a word, and the
severity glyph differs in shape as well as tone.

**It outlives the screen.** Someone reading this six weeks later has none of
the operator's context. Anything the UI said about what could NOT be checked
travels with it -- in particular the document-completeness gap, which goes
above the verdict, because a report that quietly omits "six medicines were
never assessed" reads as a clean result.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from rxconcile.export.common import (
    CATEGORY_LABEL,
    STATUS_WORD,
    ExportContext,
    canonical_by_id,
    decision_remark,
    decision_word,
    discrepancy_groups,
    document_gaps,
    row_key,
    short_remark,
    status_word,
    unchecked_line,
)
from rxconcile.export.common import (
    money as money_str,
)
from rxconcile.models import BilledItem, PrescribedItem

INK = colors.HexColor("#141A18")
MUTED = colors.HexColor("#5A635F")
RULE = colors.HexColor("#C8CEC9")
BAND = colors.HexColor("#EEF0ED")

_base = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=_base["Title"], fontName="Helvetica-Bold", fontSize=17,
                    alignment=TA_LEFT, textColor=INK, spaceAfter=2)
H2 = ParagraphStyle("H2", parent=_base["Heading2"], fontName="Helvetica-Bold", fontSize=10,
                    textColor=MUTED, spaceBefore=12, spaceAfter=5)
BODY = ParagraphStyle("Body", parent=_base["BodyText"], fontName="Helvetica", fontSize=9,
                      leading=12.5, textColor=INK)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=7.6, leading=10, textColor=MUTED)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=7.4, leading=9.5)
GAP_TITLE = ParagraphStyle("GapTitle", parent=BODY, fontName="Helvetica-Bold", fontSize=10)
#: Header cells wrap. As plain strings they overran their columns and printed
#: "FORM (RX" on top of the next heading.
HEAD = ParagraphStyle("Head", parent=BODY, fontName="Helvetica-Bold", fontSize=6.8,
                      leading=8.4, textColor=MUTED)


def _p(text: str, style: ParagraphStyle = CELL) -> Paragraph:
    return Paragraph(text if text else "&mdash;", style)


#: A report row: header strings and Paragraph cells share one list.
Row = list[Any]


def _table(data: list[Row], widths: list[float], *, head: bool = True) -> Table:
    if head and data:
        data = [
            [Paragraph(cell, HEAD) if isinstance(cell, str) else cell for cell in data[0]],
            *data[1:],
        ]
    style: list[Any] = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if head:
        style.append(("BACKGROUND", (0, 0), (-1, 0), BAND))
    return Table(data, colWidths=widths, repeatRows=1 if head else 0, style=TableStyle(style))


def _footer(canvas: Canvas, doc: SimpleDocTemplate) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 6.6)
    canvas.setFillColor(MUTED)
    width, _ = A4
    canvas.drawRightString(width - 18 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _decision(context: ExportContext, key: str) -> str:
    """One line's decision, with the reviewer's reason if they gave one.

    Word and reason share a cell: a rejection without its reason is the half of
    the record that is no use to whoever reads the report afterwards.
    """
    word = decision_word(context.decisions, key)
    remark = decision_remark(context.decisions, key)
    return f"{word} — {remark}" if remark else word


def _strength(item: PrescribedItem | BilledItem | None) -> str:
    value = getattr(item, "strength_value", None)
    if value is None:
        return "—"
    return f"{value}{getattr(item, 'strength_unit', '') or ''}"


def build_pdf(context: ExportContext) -> bytes:
    result = context.result
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=20 * mm,
        title="rxconcile reconciliation report",
        author="rxconcile",
    )
    width = doc.width
    story: list[Any] = []

    # ---- header ----
    story.append(Paragraph("Reconciliation report", H1))
    story.append(Paragraph(
        "Comparison of a pharmacy bill against the prescription it was dispensed from.",
        SMALL,
    ))
    story.append(Spacer(1, 8))
    meta = [
        ["Employee", context.employee_name or "—", "Date", context.when or "—"],
        ["Number", context.employee_number or "—", "Runs", str(context.extraction_runs or "—")],
        ["Prescription", context.prescription_filename or "—",
         "Bill", context.bill_filename or "—"],
    ]
    story.append(_table(
        [[_p(c, SMALL) for c in row] for row in meta],
        [width * 0.14, width * 0.36, width * 0.12, width * 0.38],
        head=False,
    ))

    # ---- documents not supplied: ABOVE the verdict, deliberately ----
    for title, detail in document_gaps(result):
        story.append(Spacer(1, 10))
        block = _table(
            [[_p(f"NOT ASSESSED — {title}", GAP_TITLE)], [_p(detail, BODY)]],
            [width], head=False,
        )
        block.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 1.1, INK),
            ("BACKGROUND", (0, 0), (-1, -1), BAND),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(block)

    # ---- verdict ----
    story.append(Paragraph("Summary", H2))
    # Counted by ITEM, matching the screen and the list below it. Counting raw
    # findings said "7" where two of them were a second sentence about a
    # medicine already listed.
    found = discrepancy_groups(result)
    criticals = sum(1 for g in found if g.severity == "critical")
    not_run = result.review_summary.checks_unavailable
    if result.verdict == "inconclusive":
        headline = "Could not read reliably"
        support = ("These documents could not be read consistently enough to compare. This is "
                   "not a finding that they match, and not a finding that they differ.")
    elif not found:
        headline = "No discrepancies found" + (
            " in the checks that ran" if not_run else ""
        )
        support = (f"{not_run} check(s) could not be completed and are listed below."
                   if not_run else "Every check completed and nothing was found.")
    else:
        headline = f"{len(found)} discrepanc{'y' if len(found) == 1 else 'ies'} found"
        support = (f"{criticals} serious." if criticals else "None are serious.") + \
            " Each is listed below."
    story.append(Paragraph(f"<b>{headline}</b>", BODY))
    story.append(Paragraph(support, SMALL))

    # ---- discrepancies ----
    if found:
        story.append(Paragraph("What is wrong", H2))
        rows: list[Row] = [["STATUS", "WHAT IT SAYS", "RULE"]]
        for group in found:
            message = group.headline.message
            if group.extra:
                message += f" (+{group.extra} more)"
            rows.append([
                _p(STATUS_WORD[group.severity], CELL),
                _p(message),
                _p(group.headline.rule_code),
            ])
        story.append(_table(rows, [width * 0.12, width * 0.66, width * 0.22]))

    # ---- reimbursement ----
    purse = result.reimbursement
    story.append(Paragraph("Reimbursement", H2))
    story.append(Spacer(1, 2))
    if context.claimed_amount is not None:
        story.append(Paragraph(
            f"<b>Accepted for this claim: "
            f"{money_str(purse.currency, context.claimed_amount)}</b>", BODY,
        ))
        story.append(Paragraph(
            "The total of the lines a reviewer accepted, from the Decision column below. "
            "Lines not on the prescription and lines that are not medicines are never "
            "part of it. Accepting a line records a judgement; it does not settle it.",
            SMALL,
        ))
        story.append(Spacer(1, 6))
    totals: list[Row] = [["", "AMOUNT", "LINES"]]
    for key, total, count in (
        ("eligible", purse.eligible_total, purse.eligible_line_count),
        ("not_eligible", purse.not_eligible_total, purse.not_eligible_line_count),
        ("needs_review", purse.needs_review_total, purse.needs_review_line_count),
        ("non_medicine", purse.non_medicine_total, purse.non_medicine_line_count),
    ):
        totals.append([
            _p(CATEGORY_LABEL[key], BODY),
            _p(money_str(result.reimbursement.currency, total), BODY),
            _p(str(count)),
        ])
    story.append(_table(totals, [width * 0.56, width * 0.26, width * 0.18]))
    if purse.lines_without_amount:
        story.append(Paragraph(
            f"{purse.lines_without_amount} billed line(s) print no amount. They are excluded "
            "from these totals and are not counted as zero.", SMALL,
        ))
    if purse.lines:
        rows = [["ITEM", "AMOUNT", "CATEGORY", "WHY"]]
        for line in purse.lines:
            rows.append([
                _p(line.description),
                _p(money_str(purse.currency, line.amount)),
                _p(CATEGORY_LABEL[line.category]),
                _p(line.reason),
            ])
        story.append(Spacer(1, 4))
        story.append(_table(rows, [width * 0.24, width * 0.16, width * 0.24, width * 0.36]))
    explanation = unchecked_line(result)
    if explanation:
        story.append(Spacer(1, 4))
        story.append(Paragraph(explanation, SMALL))

    # ---- medicines ----
    story.append(PageBreak())
    story.append(Paragraph("Medicines", H2))
    canonical = canonical_by_id(result)
    rx = {i.item_id: i for i in result.prescription.items}
    bill = {i.item_id: i for i in result.bill.items}
    pairs: list[tuple[str | None, str | None]] = [
        (p.prescribed_id, p.billed_id) for p in result.matched_pairs
    ]
    pairs += [(i, None) for i in result.unmatched_prescribed]
    pairs += [(None, i) for i in result.unmatched_billed]

    # Short heads on purpose: "STRENGTH prescribed" broke as "STREN/GTH prescri/bed"
    # in a column only wide enough for the value beneath it.
    rows = [["STATUS", "DRUG<br/>Rx", "DRUG<br/>Bill", "SALT", "STR<br/>Rx",
             "STR<br/>Bill", "FORM<br/>Bill", "REMARK", "DECISION"]]
    for rx_id, bill_id in pairs:
        found_row = [
            f for f in result.findings
            if (rx_id and f.prescribed_ref == rx_id) or (bill_id and f.billed_ref == bill_id)
        ]
        rx_item, bill_item = rx.get(rx_id or ""), bill.get(bill_id or "")
        rx_match, bill_match = canonical.get(rx_id or ""), canonical.get(bill_id or "")
        salt = (rx_match.salt if rx_match else None) or (bill_match.salt if bill_match else None)
        rows.append([
            _p(status_word(found_row)),
            _p(getattr(rx_item, "drug_name", None) or "—"),
            _p(getattr(bill_item, "drug_name", None) or "—"),
            _p(salt or "—"),
            _p(_strength(rx_item) if rx_item else "—"),
            _p(_strength(bill_item) if bill_item else "—"),
            _p(getattr(bill_item, "form", None) or "—"),
            _p(short_remark(found_row)),
            _p(_decision(context, row_key(rx_id, bill_id))),
        ])
    if len(rows) > 1:
        story.append(_table(rows, [
            width * 0.08, width * 0.11, width * 0.13, width * 0.13,
            width * 0.07, width * 0.07, width * 0.07, width * 0.18, width * 0.16,
        ]))
    else:
        story.append(Paragraph("Neither document carries a medicine line.", SMALL))

    # ---- lab tests ----
    story.append(Paragraph("Lab tests", H2))
    if not result.prescription.tests and not result.bill.tests:
        present = result.prescription.investigations_present
        story.append(Paragraph(
            "No investigations ordered on this prescription. Nothing to compare, and "
            "nothing missing." if present is False else
            "<b>An investigations section is present but could not be read.</b> This is not "
            "a finding that no tests were ordered — it is a finding that what was ordered "
            "is unknown." if present else
            "No investigations section was found, but its presence could not be confirmed.",
            BODY,
        ))
    else:
        rx_t = {t.item_id: t for t in result.prescription.tests}
        bill_t = {t.item_id: t for t in result.bill.tests}
        # The third element marks a line billed under an ordered panel. It is
        # carried rather than re-derived because the row key differs, and a
        # decision recorded against it would otherwise never be found.
        tpairs: list[tuple[str | None, str | None, bool]] = [
            (p.prescribed_id, p.billed_id, False) for p in result.matched_tests
        ]
        tpairs += [(i, None, False) for i in result.unmatched_prescribed_tests]
        tpairs += [(None, i, False) for i in result.unmatched_billed_tests]
        accounted = {p.billed_id for p in result.matched_tests} | set(
            result.unmatched_billed_tests
        )
        tpairs += [
            (None, t.item_id, True) for t in result.bill.tests if t.item_id not in accounted
        ]

        rows = [["STATUS", "TEST<br/>Rx", "TEST<br/>Bill", "PANEL", "REMARK", "DECISION"]]
        for rx_id, bill_id, covered in tpairs:
            found_row = [
                f for f in result.findings
                if (rx_id and f.prescribed_ref == rx_id) or (bill_id and f.billed_ref == bill_id)
            ]
            panel = next(
                (str(f.detail.get("panel") or f.detail.get("resolved_as"))
                 for f in found_row if f.detail.get("panel") or f.detail.get("resolved_as")),
                None,
            )
            remark = short_remark(found_row)
            if not found_row and rx_id is None and bill_id is not None:
                remark = "Billed as part of an ordered panel"
            rows.append([
                _p(status_word(found_row)),
                _p(getattr(rx_t.get(rx_id or ""), "test_name", None) or "—"),
                _p(getattr(bill_t.get(bill_id or ""), "test_name", None) or "—"),
                _p(panel or "—"),
                _p(remark or "—"),
                _p(_decision(context, row_key(rx_id, bill_id, tests=True, covered=covered))),
            ])
        story.append(_table(rows, [
            width * 0.11, width * 0.18, width * 0.18, width * 0.15, width * 0.22, width * 0.16,
        ]))

    # ---- source pages ----
    pages = [
        ("Prescription", context.prescription_image),
        ("Bill", context.bill_image),
    ]
    if any(data for _, data in pages):
        story.append(PageBreak())
        story.append(Paragraph("Source documents", H2))
        story.append(Paragraph(
            "The pages exactly as the extractor saw them, after preprocessing.", SMALL,
        ))
        for label, data in pages:
            if not data:
                continue
            try:
                picture = Image(BytesIO(data))
            except Exception:  # noqa: BLE001 - a bad image must not lose the report
                continue
            ratio = picture.imageHeight / picture.imageWidth
            picture.drawWidth = width
            picture.drawHeight = width * ratio
            cap = doc.height - 26 * mm
            if picture.drawHeight > cap:
                picture.drawHeight = cap
                picture.drawWidth = cap / ratio
            story.append(Spacer(1, 8))
            story.append(KeepTogether([Paragraph(f"<b>{label}</b>", BODY), Spacer(1, 4), picture]))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
