"""Persistence for completed reconciliations.

The full :class:`ReconciliationResult` is stored verbatim as JSON, with a few
indexed summary columns alongside it for listing and filtering.

That is a deliberate choice rather than laziness. This schema has changed
repeatedly across the build -- ``agreement``, ``checks_unavailable``, ``bbox``
and ``units_basis`` all arrived after the contracts were first written, and lab
tests will change it again. A blob plus summary columns absorbs those changes;
a normalised findings table would need a migration for each one, and old rows
would silently lose fields that no longer had a column.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class ScanRecord(SQLModel, table=True):
    """One completed reconciliation, as it was reported at the time."""

    __tablename__ = "scan_record"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now, index=True)

    # Who ran it. The name parts are what the operator typed, which is not
    # always the account holder; user_email is the account the token was bound
    # to.
    #
    # `employee_name` STAYS, as the computed full name. It is the grouping key
    # on the overview, the substring the history filter searches, and it is
    # frozen inside stored result blobs by the duplicate-bill finding, so it
    # cannot be dropped without rewriting history. The parts are the source of
    # truth; this is what everything that wants one string reads.
    employee_name: str
    first_name: str = Field(default="")
    middle_name: str = Field(default="")
    last_name: str = Field(default="")
    employee_number: str
    user_email: str = Field(index=True)
    role: str

    # All four upload slots, so a submitter can be shown what they did and did
    # not attach. The optional two default to empty, which reads as "not
    # supplied" rather than as a missing record.
    prescription_filename: str
    bill_filename: str
    lab_report_filename: str = Field(default="")
    lab_bill_filename: str = Field(default="")

    # What the operator said this scan was about. Stored rather than derived:
    # neither is visible anywhere in the documents.
    condition: str | None = Field(default=None, index=True)
    description: str | None = Field(default=None)

    # Summary columns, indexed for listing and filtering. Every one of these is
    # derived from result_json, never supplied independently, so they cannot
    # drift from the result they describe.
    verdict: str = Field(index=True)
    discrepancy_count: int = 0
    critical_count: int = 0
    warning_count: int = 0
    checks_unavailable_count: int = 0

    result_json: str

    # What the reviewer decided, line by line, and what that came to. The
    # amount is stored rather than recomputed on read: it is the figure the
    # reviewer saw and approved, and a later change to a rule must not silently
    # rewrite history. `decisions_json` keeps the reasoning beside it.
    decisions_json: str = Field(default="{}")
    claimed_amount: Decimal = Field(default=Decimal("0"), max_digits=12, decimal_places=2)
    allowance_year: str = Field(default="", index=True)

    # The PREPROCESSED pages, exactly as the model saw them (2000px, JPEG q90).
    # Stored rather than the originals for two reasons: bounding boxes are
    # normalised against these dimensions so highlights land correctly, and a
    # PDF export is a far weaker claims artifact without the source pages
    # beside the findings. Nullable because records written before this
    # existed have none, and because a save must never fail for want of them.
    prescription_image: bytes | None = Field(default=None)
    bill_image: bytes | None = Field(default=None)
    image_media_type: str = Field(default="image/jpeg")

    # The employee's own attestation, and where the submission has got to.
    #
    # `certified_at` is null until they tick the box, which they may do after
    # the run — the reconciliation is expensive and must not be lost because
    # somebody navigated away before certifying.
    certified_by_employee: bool = Field(default=False)
    certified_at: datetime | None = Field(default=None)
    #: submitted | under_review | reviewed. A plain indexed str on the table
    #: and a Literal at the API boundary, the same shape `verdict` and `role`
    #: already use.
    review_status: str = Field(default="submitted", index=True)

    processing_ms: int = 0
    extraction_runs: int = 0


def summarise(result: dict[str, Any]) -> dict[str, int]:
    """Derive the summary columns from a result payload.

    Counts mirror how the UI groups findings: discrepancies are the critical and
    warning findings, and checks that could not run are counted separately and
    never folded in -- a check that did not run is not a finding.
    """
    findings = result.get("findings") or []
    critical = sum(1 for f in findings if f.get("severity") == "critical")
    warning = sum(1 for f in findings if f.get("severity") == "warning")
    unavailable = sum(1 for f in findings if f.get("rule_code") == "CHECK_UNAVAILABLE")
    return {
        "critical_count": critical,
        "warning_count": warning,
        "discrepancy_count": critical + warning,
        "checks_unavailable_count": unavailable,
    }


class EmployeeAllowance(SQLModel, table=True):
    """One employee's annual reimbursement allowance.

    Keyed on the employee NUMBER rather than the name: a name is typed by hand
    on every scan and will not be typed the same way twice.
    """

    __tablename__ = "employee_allowance"

    employee_number: str = Field(primary_key=True)
    employee_name: str = ""
    annual_amount: Decimal = Field(
        default=Decimal("12000.00"), max_digits=12, decimal_places=2,
        description="The yearly limit. Configurable per employee.",
    )
