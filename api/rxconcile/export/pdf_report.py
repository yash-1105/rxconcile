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

from decimal import Decimal
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from rxconcile.export.common import (
    CATEGORY_LABEL,
    STATUS_WORD,
    ExportContext,
    canonical_by_id,
    category_totals,
    decision_remark,
    decision_word,
    discrepancy_groups,
    document_gaps,
    short_remark,
)
from rxconcile.export.common import (
    money as money_str,
)
from rxconcile.export.rows import (
    STATUS_LABEL,
    TINT,
    RowState,
    counts,
    lab_remark,
    medicine_rows,
    panel_of,
    test_rows,
)
from rxconcile.models import BilledItem, PrescribedItem

INK = colors.HexColor("#141A18")
MUTED = colors.HexColor("#5A635F")
RULE = colors.HexColor("#C8CEC9")
BAND = colors.HexColor("#EEF0ED")
SEAL = colors.HexColor("#0E4F45")
FLAG = colors.HexColor("#A3231C")
CAUTION = colors.HexColor("#8A5A06")
TRACK = colors.HexColor("#E3E6E2")
SPENT = colors.HexColor("#9AA39D")

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
#: The summary page mirrors the dashboard, where two figures lead and
#: everything else recedes to a supporting scale.
HERO = ParagraphStyle("Hero", parent=BODY, fontName="Helvetica-Bold", fontSize=24, leading=27)
BIG = ParagraphStyle("Big", parent=BODY, fontName="Helvetica-Bold", fontSize=20, leading=23)
LABEL = ParagraphStyle("Label", parent=BODY, fontName="Helvetica-Bold", fontSize=6.6,
                       leading=8.5, textColor=MUTED)
VERDICT = ParagraphStyle("Verdict", parent=BODY, fontName="Helvetica-Bold", fontSize=16,
                         leading=19)


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


def _footer(canvas: Canvas, doc: BaseDocTemplate) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 6.6)
    canvas.setFillColor(MUTED)
    # From the page being drawn, not a constant: the table pages are landscape.
    page_width = float(canvas._pagesize[0])  # type: ignore[attr-defined]
    canvas.drawRightString(page_width - 18 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _bar(width: float, annual: float, used: float, claim: float) -> Table:
    """The allowance bar, as on screen: used, this claim, then what is left.

    Proportions only. An overdrawn claim FILLS the track rather than running
    past it, because a segment wider than its row would be clipped and read as
    "exactly full", which is a different statement.
    """
    if annual <= 0:
        return Table([[""]], colWidths=[width], rowHeights=[5])
    used_w = min(width, width * used / annual)
    claim_w = max(0.0, min(width * claim / annual, width - used_w))
    rest_w = max(0.0, width - used_w - claim_w)
    over = used + claim > annual
    cells, widths, styles = [], [], []
    for segment, colour in (
        (used_w, SPENT), (claim_w, FLAG if over else SEAL), (rest_w, TRACK)
    ):
        if segment <= 0.4:
            continue
        cells.append("")
        widths.append(segment)
        styles.append(("BACKGROUND", (len(cells) - 1, 0), (len(cells) - 1, 0), colour))
    bar = Table([cells], colWidths=widths, rowHeights=[5])
    bar.setStyle(TableStyle([*styles,
                             ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                             ("TOPPADDING", (0, 0), (-1, -1), 0),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return bar


def _tone(style: ParagraphStyle, colour: colors.Color) -> ParagraphStyle:
    """The same step in a different colour. Green matched, red problems."""
    return ParagraphStyle(f"{style.name}-{colour.hexval()}", parent=style, textColor=colour)


def _plain(rows: list[Row], widths: list[float], pad: int = 6) -> Table:
    """A borderless block. Structure on this page comes from tone, not rules."""
    table = Table(rows, colWidths=widths)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
    ]))
    return table


#: Which cell a finding points at. Mirrors FIELD_OF in web/src/components/Tables.tsx.
_FIELD_OF: dict[str, str] = {
    "STRENGTH_MISMATCH": "strength",
    "STRENGTH_UNIT_UNSTATED": "strength",
    "FORM_MISMATCH": "form",
    "QUANTITY_SHORT": "qty",
    "QUANTITY_EXCESS": "qty",
    "QUANTITY_AMBIGUOUS": "qty",
    "BRAND_SUBSTITUTION": "drug",
    "SALT_DIFFERENT_CLASS": "drug",
    "DUPLICATE_THERAPY": "drug",
    "SCHEDULE_H_UNBACKED": "drug",
}


def _field_marks(findings: list[Any]) -> dict[str, str]:
    """The loudest marking any finding puts on one field of a row."""
    rank = {"critical": 0, "warning": 1, "info": 2}
    out: dict[str, str] = {}
    for found in findings:
        field = _FIELD_OF.get(found.rule_code)
        if field is None:
            continue
        if field not in out or rank[found.severity] < rank[out[field]]:
            out[field] = found.severity
    return out


def _expected_qty(findings: list[Any]) -> str:
    """The quantity a course implies, as the ENGINE computed it.

    Never derived here. If no quantity rule ran there is no expectation to
    show, and the cell stays an em-dash rather than inventing one.
    """
    for found in findings:
        value = found.detail.get("expected_units")
        if isinstance(value, (int, float)):
            return str(value)
    return "—"


def _billed_qty(item: BilledItem | None) -> str:
    if item is None or item.quantity is None:
        return "—"
    return f"{item.quantity} · {item.pack_size}" if item.pack_size else str(item.quantity)


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
    # The summary reads as a page; the tables do not fit one. Thirteen columns
    # on portrait A4 wrapped "GLYCOMET" as "GLYCOM ET" and "tablet" as "tabl
    # et", which is not a report anybody can use. The tables get landscape.
    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=20 * mm,
        title="rxconcile reconciliation report", author="rxconcile",
    )
    wide = landscape(A4)
    doc.addPageTemplates([
        PageTemplate(
            id="summary", pagesize=A4, onPage=_footer,
            frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="s")],
        ),
        PageTemplate(
            id="tables", pagesize=wide, onPage=_footer,
            frames=[Frame(
                18 * mm, 20 * mm,
                wide[0] - 36 * mm, wide[1] - 36 * mm, id="t",
            )],
        ),
    ])
    width = doc.width
    table_width = wide[0] - 36 * mm
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

    # ---- the summary page, laid out as the dashboard panel is ----
    med = medicine_rows(result)
    tests = test_rows(result)
    med_counts = counts([r.state for r in med])
    test_counts = counts([r.state for r in tests])

    found = discrepancy_groups(result)
    criticals = sum(1 for g in found if g.severity == "critical")
    not_run = result.review_summary.checks_unavailable
    if result.verdict == "inconclusive":
        headline = "Could not read reliably"
        support = ("These documents could not be read consistently enough to compare. This is "
                   "not a finding that they match, and not a finding that they differ.")
    elif not found:
        headline = "No discrepancies found" + (" in the checks that ran" if not_run else "")
        support = (f"{not_run} check(s) could not be completed and are listed below."
                   if not_run else "Every check completed and nothing was found.")
    else:
        headline = f"{len(found)} discrepanc{'y' if len(found) == 1 else 'ies'} found"
        support = (
            f"{criticals} of them are serious: the bill does not match what was prescribed."
            if criticals else "None of them are serious."
        ) + " Each is listed below."

    story.append(Spacer(1, 12))
    story.append(Paragraph(headline, VERDICT))
    story.append(Paragraph(support, BODY))
    if context.result.submission and (
        context.result.submission.condition or context.result.submission.description
    ):
        bits = [b for b in (context.result.submission.condition,
                            context.result.submission.description) if b]
        story.append(Paragraph(" — ".join(bits), SMALL))

    # The four counts, two by two, as on screen.
    story.append(Spacer(1, 10))
    story.append(_plain(
        [
            [
                Paragraph("MEDICINES MATCHED", LABEL),
                Paragraph("MEDICINES WITH PROBLEMS", LABEL),
                Paragraph("LAB TESTS MATCHED", LABEL),
                Paragraph("LAB TESTS WITH PROBLEMS", LABEL),
            ],
            [
                Paragraph(str(med_counts.matched), _tone(BIG, SEAL)),
                Paragraph(str(med_counts.problems),
                          _tone(BIG, FLAG if med_counts.problems else MUTED)),
                Paragraph(str(test_counts.matched), _tone(BIG, SEAL)),
                Paragraph(str(test_counts.problems),
                          _tone(BIG, FLAG if test_counts.problems else MUTED)),
            ],
        ],
        [width * 0.25] * 4, pad=3,
    ))

    # ---- the allowance block ----
    purse = result.reimbursement
    if context.annual_amount is not None:
        annual = float(context.annual_amount)
        used = float(context.used_amount or 0)
        claim = float(context.claimed_amount or 0)
        remaining = max(0.0, annual - used - claim)
        story.append(Spacer(1, 14))
        story.append(Paragraph("BALANCE REMAINING", LABEL))
        story.append(Paragraph(
            money_str(purse.currency, Decimal(str(round(remaining, 2)))),
            _tone(HERO, FLAG if used + claim > annual else INK),
        ))
        story.append(Spacer(1, 6))
        story.append(_bar(width, annual, used, claim))
        story.append(_plain([
            [Paragraph("ALLOWANCE", LABEL), Paragraph("USED", LABEL),
             Paragraph("THIS CLAIM", LABEL)],
            [Paragraph(money_str(purse.currency, context.annual_amount), BODY),
             Paragraph(money_str(purse.currency, context.used_amount), BODY),
             Paragraph(money_str(purse.currency, context.claimed_amount),
                       _tone(BODY, SEAL))],
        ], [width / 3] * 3, pad=3))
        story.append(Paragraph(
            f"Allowance year {context.allowance_year}. Used so far excludes this claim.",
            SMALL,
        ))

    # ---- discrepancies, most serious first ----
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

    # ---- reimbursement: the same figures, then the breakdown ----
    story.append(Paragraph("Reimbursement", H2))
    totals: list[Row] = [["", "AMOUNT", "LINES"]]
    for key, total, count in category_totals(result):
        totals.append([
            _p(CATEGORY_LABEL[key], BODY),
            _p(money_str(purse.currency, total), BODY),
            _p(str(count)),
        ])
    story.append(_table(totals, [width * 0.56, width * 0.26, width * 0.18]))
    story.append(Paragraph(
        "Accepted lines only. Lines not on the prescription and lines that are not "
        "medicines are never part of the claim.", SMALL,
    ))
    if purse.lines_without_amount:
        story.append(Paragraph(
            f"{purse.lines_without_amount} billed line(s) print no amount. They are excluded "
            "from these totals and are not counted as zero.", SMALL,
        ))

    # ---- medicines: every column the dashboard shows ----
    story.append(NextPageTemplate("tables"))
    story.append(PageBreak())
    width = table_width
    story.append(Paragraph("Medicines", H2))
    canonical = canonical_by_id(result)

    def tinted(table: Table, states: list[RowState], first_row: int = 2) -> Table:
        """Row backgrounds matching the screen.

        The word in the STATUS column carries the same meaning, so the report
        survives a greyscale printer — the tint is a convenience, never the
        statement.
        """
        style = [("BACKGROUND", (0, first_row + i), (-1, first_row + i),
                  colors.HexColor(TINT[state]))
                 for i, state in enumerate(states)]
        table.setStyle(TableStyle(style))
        return table

    def marked(text: str, severity: str | None) -> Paragraph:
        """A changed value, marked as the screen marks it."""
        if not severity:
            return _p(text)
        colour = {"critical": "#A3231C", "warning": "#8A5A06"}.get(severity)
        if colour is None:
            return _p(text)
        return Paragraph(f'<font color="{colour}"><b>{text or "&mdash;"}</b></font>', CELL)

    if med:
        rows = [
            ["#", "STATUS", "REMARK", "DRUG", "", "SALT", "STRENGTH", "",
             "FORM", "", "QTY", "", "DECISION"],
            ["", "", "", "Rx", "Bill", "", "Rx", "Bill", "Rx", "Bill", "Rx", "Bill", ""],
        ]
        for index, row in enumerate(med, start=1):
            marks = _field_marks(row.findings)
            # Only mark a pair when both halves exist: on an unmatched line the
            # status already says everything, and marking a lone cell would
            # imply a comparison that never happened.
            if row.prescribed is None or row.billed is None:
                marks = {}
            rx_item, bill_item = row.prescribed, row.billed
            match_rx = canonical.get(rx_item.item_id if rx_item else "")
            match_bill = canonical.get(bill_item.item_id if bill_item else "")
            salt = (match_rx.salt if match_rx else None) or (
                match_bill.salt if match_bill else None)
            rows.append([
                _p(str(index)),
                _p(STATUS_LABEL[row.state] + ("*" if row.partial else "")),
                _p(short_remark(row.findings)),
                _p(getattr(rx_item, "drug_name", None) or "—"),
                marked(getattr(bill_item, "drug_name", None) or "—", marks.get("drug")),
                _p(salt or "—"),
                _p(_strength(rx_item) if rx_item else "—"),
                marked(_strength(bill_item) if bill_item else "—", marks.get("strength")),
                _p(getattr(rx_item, "form", None) or "—"),
                marked(getattr(bill_item, "form", None) or "—", marks.get("form")),
                _p(_expected_qty(row.findings)),
                marked(_billed_qty(bill_item), marks.get("qty")),
                _p(_decision(context, row.key)),
            ])
        table = _table(rows, [
            width * 0.028, width * 0.090, width * 0.155,
            width * 0.082, width * 0.082, width * 0.100,
            width * 0.054, width * 0.054, width * 0.048, width * 0.048,
            width * 0.046, width * 0.046, width * 0.097,
        ])
        table.setStyle(TableStyle([
            ("SPAN", (3, 0), (4, 0)), ("SPAN", (6, 0), (7, 0)),
            ("SPAN", (8, 0), (9, 0)), ("SPAN", (10, 0), (11, 0)),
            ("SPAN", (0, 0), (0, 1)), ("SPAN", (1, 0), (1, 1)),
            ("SPAN", (2, 0), (2, 1)), ("SPAN", (5, 0), (5, 1)),
            ("SPAN", (12, 0), (12, 1)),
            ("ALIGN", (3, 0), (11, 0), "CENTER"),
        ]))
        story.append(tinted(table, [r.state for r in med]))
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
        rows = [
            ["#", "STATUS", "REMARK", "TEST", "", "PANEL", "DECISION"],
            ["", "", "", "Rx", "Bill", "", ""],
        ]
        for index, trow in enumerate(tests, start=1):
            # A component sits under its parent, named and indented, exactly as
            # the screen groups it.
            ordered = (
                f"&nbsp;&nbsp;&nbsp;&#183; {trow.covered_by}" if trow.covered_by
                else (getattr(trow.prescribed, "test_name", None) or "—")
            )
            rows.append([
                _p(str(index)),
                _p(STATUS_LABEL[trow.state] + ("*" if trow.partial else "")),
                _p(lab_remark(trow, short_remark)),
                _p(ordered),
                _p(getattr(trow.billed, "test_name", None) or "—"),
                _p(panel_of(trow) or "—"),
                _p(_decision(context, trow.key)),
            ])
        table = _table(rows, [
            width * 0.035, width * 0.095, width * 0.215,
            width * 0.175, width * 0.190, width * 0.130, width * 0.160,
        ])
        table.setStyle(TableStyle([
            ("SPAN", (3, 0), (4, 0)),
            ("SPAN", (0, 0), (0, 1)), ("SPAN", (1, 0), (1, 1)), ("SPAN", (2, 0), (2, 1)),
            ("SPAN", (5, 0), (5, 1)), ("SPAN", (6, 0), (6, 1)),
            ("ALIGN", (3, 0), (4, 0), "CENTER"),
        ]))
        story.append(tinted(table, [r.state for r in tests]))

    # ---- source pages ----
    width = doc.width
    story.append(NextPageTemplate("summary"))
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

    doc.build(story)
    return buffer.getvalue()
