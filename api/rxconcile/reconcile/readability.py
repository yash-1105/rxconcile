"""What could be READ off each uploaded document.

The one thing an employee is told about their own submission. They do not see
the reconciliation — that is a reviewer's job — but a photograph nobody could
read has to come back to them at once, or the claim fails silently days later
at review and they never learn why.

So this answers exactly one question per document: could we read it. It is
built from extraction signals only:

  * `ITEM_COUNT_UNSTABLE` — the only rule code that names a document
  * `Prescription.warnings` / `PharmacyBill.warnings` — per-document prose the
    extractor already wrote for a human
  * `run_item_counts` / `unstable_lines` — runs disagreeing about the page
  * `investigations_present` with nothing read from that section
  * `Submission` — which slots were filled, and what came off the lab bill

It must NEVER read a reconciliation finding. "Telma was billed at 20mg against
40mg prescribed" is a perfectly readable document with a real discrepancy on
it, and telling an employee to re-photograph it would be wrong twice over:
their photo was fine, and the discrepancy is not theirs to see.
`tests/test_readability.py` asserts that separation directly.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, Field

from rxconcile.models import ReconciliationResult

DocumentSlot = Literal["prescription", "pharmacy_bill", "lab_bill", "lab_report"]

#: What we are able to say about one uploaded document.
#:
#: `not_assessed` is its own answer and not a euphemism for "fine": a lab
#: report is filed with the claim and never read, and saying so is the only
#: honest thing. A check that could not run must never render as one that
#: passed.
ReadState = Literal["read", "partly_unreadable", "unreadable", "not_assessed", "not_supplied"]

SLOT_LABEL: Final[dict[DocumentSlot, str]] = {
    "prescription": "Prescription",
    "pharmacy_bill": "Pharmacy bill",
    "lab_bill": "Lab bill",
    "lab_report": "Lab report",
}

#: The one code that names a document rather than a line.
_UNSTABLE: Final[str] = "ITEM_COUNT_UNSTABLE"


class DocumentReadability(BaseModel):
    """One uploaded document, and whether it could be read."""

    slot: DocumentSlot
    label: str
    supplied: bool
    state: ReadState
    #: What the employee should do about it, or None when there is nothing to do.
    message: str | None = None
    #: The extractor's own words, when it had any. Never a reconciliation
    #: finding.
    detail: list[str] = Field(default_factory=list)

    @property
    def needs_action(self) -> bool:
        return self.state in {"unreadable", "partly_unreadable"}


def _unstable_documents(result: ReconciliationResult) -> set[str]:
    """Documents whose extraction runs disagreed on how many lines they hold."""
    names: set[str] = set()
    for finding in result.findings:
        if finding.rule_code != _UNSTABLE:
            continue
        document = finding.detail.get("document")
        if isinstance(document, str):
            names.add(document)
    return names


def _prescription(result: ReconciliationResult) -> DocumentReadability:
    page = result.prescription
    supplied = result.submission.prescription_supplied
    if not supplied:
        return DocumentReadability(
            slot="prescription", label=SLOT_LABEL["prescription"], supplied=False,
            state="not_supplied",
            message="No prescription was uploaded. One is required.",
        )

    unstable = "prescription" in _unstable_documents(result)
    nothing_read = not page.items and not page.tests
    # Inconclusive is decided from the prescription's own legibility -- the
    # share of items with no drug name, and how far the runs agreed. It says
    # the page could not be read, not that the documents disagree.
    inconclusive = result.verdict == "inconclusive"

    if nothing_read or inconclusive:
        return DocumentReadability(
            slot="prescription", label=SLOT_LABEL["prescription"], supplied=True,
            state="unreadable",
            message="We could not read the prescription clearly. Please upload a sharper "
                    "photo — flat, square on, and in even light.",
            detail=list(page.warnings),
        )
    if unstable or page.unstable_lines:
        return DocumentReadability(
            slot="prescription", label=SLOT_LABEL["prescription"], supplied=True,
            state="partly_unreadable",
            message="Some lines on the prescription came out differently each time we read "
                    "it. A sharper photo would make the claim easier to review.",
            detail=list(page.warnings),
        )
    # An investigations section that is present and unreadable is a real gap,
    # and one only the employee can fix.
    if page.investigations_present and not page.tests:
        return DocumentReadability(
            slot="prescription", label=SLOT_LABEL["prescription"], supplied=True,
            state="partly_unreadable",
            message="The prescription has a tests section we could not read. If tests were "
                    "ordered, please upload a clearer photo of that part.",
            detail=list(page.warnings),
        )
    return DocumentReadability(
        slot="prescription", label=SLOT_LABEL["prescription"], supplied=True,
        state="read", detail=list(page.warnings),
    )


def _pharmacy_bill(result: ReconciliationResult) -> DocumentReadability:
    page = result.bill
    supplied = result.submission.pharmacy_bill_supplied
    if not supplied:
        return DocumentReadability(
            slot="pharmacy_bill", label=SLOT_LABEL["pharmacy_bill"], supplied=False,
            state="not_supplied",
            message="No pharmacy bill was uploaded. One is required.",
        )

    unstable = "bill" in _unstable_documents(result)
    if not page.items:
        return DocumentReadability(
            slot="pharmacy_bill", label=SLOT_LABEL["pharmacy_bill"], supplied=True,
            state="unreadable",
            message="We could not read the pharmacy bill clearly. Please upload a sharper "
                    "photo — flat, square on, and in even light.",
            detail=list(page.warnings),
        )
    if unstable or page.unstable_lines:
        return DocumentReadability(
            slot="pharmacy_bill", label=SLOT_LABEL["pharmacy_bill"], supplied=True,
            state="partly_unreadable",
            message="Some lines on the pharmacy bill came out differently each time we read "
                    "it. A sharper photo would make the claim easier to review.",
            detail=list(page.warnings),
        )
    return DocumentReadability(
        slot="pharmacy_bill", label=SLOT_LABEL["pharmacy_bill"], supplied=True,
        state="read", detail=list(page.warnings),
    )


def _lab_bill(result: ReconciliationResult) -> DocumentReadability:
    submission = result.submission
    if not submission.lab_bill_supplied:
        return DocumentReadability(
            slot="lab_bill", label=SLOT_LABEL["lab_bill"], supplied=False,
            state="not_supplied",
        )
    if submission.lab_bill_tests_read is None:
        # Recorded before this was measured. Saying it was fine would be a
        # check reported as passed when it never ran.
        return DocumentReadability(
            slot="lab_bill", label=SLOT_LABEL["lab_bill"], supplied=True,
            state="not_assessed",
            message="This claim was submitted before we recorded what came off a lab "
                    "bill, so nothing here says whether it could be read.",
        )
    if submission.lab_bill_tests_read == 0:
        return DocumentReadability(
            slot="lab_bill", label=SLOT_LABEL["lab_bill"], supplied=True,
            state="unreadable",
            message="We could not read any test line on the lab bill. Please upload a "
                    "sharper photo — flat, square on, and in even light.",
            detail=list(submission.lab_bill_warnings),
        )
    if submission.lab_bill_unstable:
        return DocumentReadability(
            slot="lab_bill", label=SLOT_LABEL["lab_bill"], supplied=True,
            state="partly_unreadable",
            message="Some lines on the lab bill came out differently each time we read it. "
                    "A sharper photo would make the claim easier to review.",
            detail=list(submission.lab_bill_warnings),
        )
    return DocumentReadability(
        slot="lab_bill", label=SLOT_LABEL["lab_bill"], supplied=True,
        state="read", detail=list(submission.lab_bill_warnings),
    )


def _lab_report(result: ReconciliationResult) -> DocumentReadability:
    """Filed, never read.

    A lab report carries results, not charges, so nothing on it is compared to
    anything. Reporting it as "read" would claim a check that never happened.
    """
    if not result.submission.lab_report_supplied:
        return DocumentReadability(
            slot="lab_report", label=SLOT_LABEL["lab_report"], supplied=False,
            state="not_supplied",
        )
    return DocumentReadability(
        slot="lab_report", label=SLOT_LABEL["lab_report"], supplied=True,
        state="not_assessed",
        message="Filed with your claim. Lab reports are not read by this system, so nothing "
                "here says whether it is legible.",
    )


def readability_of(result: ReconciliationResult) -> list[DocumentReadability]:
    """Every uploaded slot, in the order the upload form shows them."""
    return [
        _prescription(result),
        _pharmacy_bill(result),
        _lab_bill(result),
        _lab_report(result),
    ]


def unavailable() -> list[DocumentReadability]:
    """When the stored result cannot be read back at all.

    A record written before a schema change may no longer validate. Saying
    nothing about readability is correct; claiming the documents were fine
    would be a check reported as passed when it never ran.
    """
    return [
        DocumentReadability(
            slot=slot, label=SLOT_LABEL[slot], supplied=True, state="not_assessed",
            message="This submission was recorded in an older format, so nothing here "
                    "says whether the document could be read.",
        )
        for slot in ("prescription", "pharmacy_bill")
    ]
