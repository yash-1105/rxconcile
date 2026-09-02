"""What was read off a submitter's own documents, transcribed.

Transparency, not analysis. A submitter is entitled to see what the system made
of the paper they handed over — that is how they spot a misread strength or a
line we missed. They are not entitled to the comparison, which is a reviewer's
work and is not in this module at all.

The boundary is hard and is enforced by SHAPE, not by discipline: these models
are an allow-list built field by field out of the extracted documents. Nothing
here can carry a verdict, a rule code, a finding, a matched/unmatched status or
a prescribed-versus-billed pairing, because there is nowhere to put one. A
medicine on the bill that was never prescribed comes out looking exactly like
one that was, which is the point.

Extraction metadata is left out too — `agreement`, `confidence`, `bbox`. It is
not comparison, but it is not something a submitter can act on either, and the
readability summary already says what they need to know.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from rxconcile.models import (
    BilledItem,
    BilledTest,
    LabReport,
    PharmacyBill,
    PrescribedItem,
    PrescribedTest,
    Prescription,
    ReconciliationResult,
    ReportedTest,
)


def _money(amount: Decimal | None) -> str | None:
    """Amounts as strings. Null stays null: a line with no printed amount was
    not free, and rendering it as 0.00 would invent a figure."""
    return None if amount is None else str(amount)


def _number(value: float | None) -> str | None:
    return None if value is None else str(value)


class PrescribedLine(BaseModel):
    """One medicine, as written on the prescription."""

    name: str | None = None
    strength: str | None = None
    form: str | None = None
    frequency: str | None = None
    duration: str | None = None
    #: The line as it appears on the page, so nothing that was read is lost to
    #: a field this model does not have.
    raw_text: str = ""


class PrescriptionContent(BaseModel):
    prescriber: str | None = None
    clinic: str | None = None
    date: str | None = None
    patient_name: str | None = None
    patient_age: str | None = None
    patient_sex: str | None = None
    medicines: list[PrescribedLine] = Field(default_factory=list)
    investigations: list[str] = Field(default_factory=list)


class BilledLine(BaseModel):
    """One line on a pharmacy bill."""

    item: str | None = None
    batch: str | None = None
    expiry: str | None = None
    pack: str | None = None
    quantity: str | None = None
    rate: str | None = None
    amount: str | None = None
    raw_text: str = ""


class BillContent(BaseModel):
    name: str | None = None
    bill_no: str | None = None
    bill_date: str | None = None
    lines: list[BilledLine] = Field(default_factory=list)
    subtotal: str | None = None
    tax: str | None = None
    grand_total: str | None = None
    currency: str = "INR"


class LabLine(BaseModel):
    test: str | None = None
    amount: str | None = None
    raw_text: str = ""


class LabBillContent(BaseModel):
    name: str | None = None
    bill_no: str | None = None
    bill_date: str | None = None
    tests: list[LabLine] = Field(default_factory=list)
    subtotal: str | None = None
    tax: str | None = None
    grand_total: str | None = None
    currency: str = "INR"


def _strength(item: PrescribedItem | BilledItem) -> str | None:
    if item.strength_value is None:
        return None
    return f"{item.strength_value}{item.strength_unit or ''}"


def _prescribed_line(item: PrescribedItem) -> PrescribedLine:
    return PrescribedLine(
        name=item.drug_name,
        strength=_strength(item),
        form=item.form,
        frequency=item.frequency_raw,
        duration=item.duration_raw,
        raw_text=item.raw_text,
    )


def prescription_content(page: Prescription) -> PrescriptionContent:
    return PrescriptionContent(
        prescriber=page.prescriber_name,
        clinic=page.clinic_name,
        date=str(page.date_issued) if page.date_issued else None,
        patient_name=page.patient_name,
        patient_age=page.patient_age,
        patient_sex=page.patient_sex,
        medicines=[_prescribed_line(item) for item in page.items],
        investigations=[
            test.test_name or test.raw_text for test in _ordered_tests(page.tests)
        ],
    )


def _ordered_tests(tests: list[PrescribedTest]) -> list[PrescribedTest]:
    return list(tests)


def _billed_line(item: BilledItem) -> BilledLine:
    return BilledLine(
        item=item.drug_name,
        batch=item.batch_no,
        expiry=str(item.expiry) if item.expiry else None,
        pack=item.pack_size,
        quantity=_number(item.quantity),
        rate=_money(item.unit_price),
        amount=_money(item.line_total),
        raw_text=item.raw_text,
    )


def _lab_line(test: BilledTest) -> LabLine:
    return LabLine(
        test=test.test_name, amount=_money(test.line_total), raw_text=test.raw_text
    )


def bill_content(bill: PharmacyBill, *, exclude_tests: set[str]) -> BillContent:
    """The pharmacy bill as read.

    `exclude_tests` are the lines a separately uploaded lab bill contributed.
    They are shown under that document instead, so nothing appears twice.
    """
    lines = [_billed_line(item) for item in bill.items]
    lines += [
        BilledLine(
            item=test.test_name,
            quantity=_number(test.quantity),
            rate=_money(test.unit_price),
            amount=_money(test.line_total),
            raw_text=test.raw_text,
        )
        for test in bill.tests
        if test.item_id not in exclude_tests
    ]
    return BillContent(
        name=bill.pharmacy_name,
        bill_no=bill.bill_no,
        bill_date=str(bill.bill_date) if bill.bill_date else None,
        lines=lines,
        subtotal=_money(bill.subtotal),
        tax=_money(bill.tax_total),
        grand_total=_money(bill.grand_total),
        currency=bill.currency,
    )


def lab_bill_content(bill: PharmacyBill) -> LabBillContent:
    return LabBillContent(
        name=bill.pharmacy_name,
        bill_no=bill.bill_no,
        bill_date=str(bill.bill_date) if bill.bill_date else None,
        tests=[_lab_line(test) for test in bill.tests],
        subtotal=_money(bill.subtotal),
        tax=_money(bill.tax_total),
        grand_total=_money(bill.grand_total),
        currency=bill.currency,
    )


def _document_total(bill: PharmacyBill, *, lines: list[BilledLine]) -> Decimal | None:
    """What one bill comes to.

    Its own printed grand total if it has one — that is the document's own
    figure and is complete by definition. Otherwise the lines are summed, and
    only when EVERY line printed an amount: a total quietly missing a line is
    worse than no total, because nothing on screen would say it was short.
    """
    if bill.grand_total is not None:
        return bill.grand_total
    if not lines:
        return None
    if any(line.amount is None for line in lines):
        return None
    return sum((Decimal(line.amount or "0") for line in lines), Decimal("0"))


def billed_total(result: ReconciliationResult) -> str | None:
    """The total on the documents the submitter uploaded.

    NOT a claimable, eligible or supported figure. Those come from the
    comparison, they move when a reviewer rejects a line, and showing one would
    promise an amount nobody has agreed to.

    Null when it cannot be stated honestly — a bill that prints no total and
    has a line with no amount on it. The screen then says so rather than
    printing a number that is quietly short.
    """
    lab = result.submission.lab_bill
    merged = set(result.submission.lab_bill_merged_ids)
    pharmacy = _document_total(
        result.bill, lines=bill_content(result.bill, exclude_tests=merged).lines
    )
    if pharmacy is None:
        return None
    if lab is None:
        return str(pharmacy)
    lab_total = _document_total(lab, lines=[
        BilledLine(amount=_money(test.line_total)) for test in lab.tests
    ])
    if lab_total is None:
        return None
    return str(pharmacy + lab_total)


class ReportedLine(BaseModel):
    """One result line, transcribed.

    Result, unit, range and the lab's own flag, exactly as printed. There is no
    field here for whether a value is high or low, and there must not be:
    hard rule 10 puts that out of scope, and a submitter reading their own
    results must see the laboratory's words, not this system's opinion of them.
    """

    test: str | None = None
    panel: str | None = None
    result: str | None = None
    unit: str | None = None
    reference_range: str | None = None
    #: The flag THE LAB printed, or null. Null is not "normal".
    flag: str | None = None
    page: int | None = None
    raw_text: str = ""


class LabReportContent(BaseModel):
    lab_name: str | None = None
    report_number: str | None = None
    patient_name: str | None = None
    referred_by: str | None = None
    collected_date: str | None = None
    reported_date: str | None = None
    tests: list[ReportedLine] = Field(default_factory=list)
    page_count: int | None = None


def _reported_line(test: ReportedTest) -> ReportedLine:
    return ReportedLine(
        test=test.test_name,
        panel=test.panel,
        result=test.result_value,
        unit=test.unit,
        reference_range=test.reference_range,
        flag=test.lab_flag,
        page=test.page,
        raw_text=test.raw_text,
    )


def lab_report_content(report: LabReport) -> LabReportContent:
    return LabReportContent(
        lab_name=report.lab_name,
        report_number=report.report_number,
        patient_name=report.patient_name,
        referred_by=report.referred_by,
        collected_date=str(report.collected_date) if report.collected_date else None,
        reported_date=str(report.reported_date) if report.reported_date else None,
        tests=[_reported_line(test) for test in report.tests],
        page_count=report.page_count,
    )


class ExtractedContent(BaseModel):
    """Everything a submitter is shown about their own documents."""

    prescription: PrescriptionContent
    pharmacy_bill: BillContent
    lab_bill: LabBillContent | None = None
    #: What was read off the lab report. Null when none was uploaded. Carries
    #: no comparison: which tests were ordered or charged for is a reviewer's
    #: question, and this is the submitter's own document read back to them.
    lab_report: LabReportContent | None = None
    #: The total on their documents. See `billed_total` for why this is not a
    #: reimbursable figure and never becomes one.
    billed_total: str | None = None
    currency: str = "INR"


def extracted_content(result: ReconciliationResult) -> ExtractedContent:
    lab = result.submission.lab_bill
    report = result.submission.lab_report
    merged = set(result.submission.lab_bill_merged_ids)
    return ExtractedContent(
        prescription=prescription_content(result.prescription),
        pharmacy_bill=bill_content(result.bill, exclude_tests=merged),
        lab_bill=lab_bill_content(lab) if lab is not None else None,
        lab_report=lab_report_content(report) if report is not None else None,
        billed_total=billed_total(result),
        currency=result.bill.currency,
    )
