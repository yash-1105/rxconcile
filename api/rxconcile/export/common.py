"""Shared export vocabulary."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict

from rxconcile.models import CanonicalMatch, Finding, ReconciliationResult

DISCLAIMER: Final[str] = (
    "Proof of concept. Automated document comparison only, not clinical verification "
    "and not an insurance determination. Nothing in this report approves or rejects "
    "anything. All findings require human review."
)

REIMBURSEMENT_NOTE: Final[str] = (
    "An assessment of which billed items are supported by the prescription. Coverage "
    "rules, copay tiers and policy limits appear in neither document, are not modelled, "
    "and are not inferred."
)

#: Status words. Never a colour on its own -- these reports must survive being
#: printed in greyscale, so every status is legible as text.
STATUS_WORD: Final[dict[str, str]] = {
    "critical": "PROBLEM",
    "warning": "CHECK",
    "info": "NOTED",
}

CATEGORY_LABEL: Final[dict[str, str]] = {
    "eligible": "Supported by the prescription",
    "not_eligible": "Not supported by the prescription",
    "needs_review": "Needs review",
}


def money(currency: str, amount: Decimal | None) -> str:
    """Format an amount for a report.

    None is "not printed", never 0.00: a bill line with no amount was not free.
    """
    if amount is None:
        return "not printed"
    return f"{currency} {amount.quantize(Decimal('0.01')):,}"


class ExportContext(BaseModel):
    """Everything a report needs that is not in the result itself."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    result: ReconciliationResult
    employee_name: str = ""
    employee_number: str = ""
    created_at: datetime | None = None
    prescription_filename: str = ""
    bill_filename: str = ""
    scan_id: int | None = None
    extraction_runs: int = 0
    prescription_image: bytes | None = None
    bill_image: bytes | None = None

    @property
    def when(self) -> str:
        return self.created_at.strftime("%d %b %Y, %H:%M") if self.created_at else ""


def document_gaps(result: ReconciliationResult) -> list[tuple[str, str]]:
    """Documents that were not supplied, and what went unassessed as a result.

    The screen shows this above the verdict. A report that omitted it would let
    a reader six weeks later assume every line was examined.
    """
    gaps: list[tuple[str, str]] = []

    unassessed = [
        f for f in result.findings
        if f.rule_code == "RX_NOT_BILLED" and f.detail.get("lab_only_bill") is True
    ]
    if unassessed:
        count = len(unassessed)
        gaps.append((
            "The pharmacy bill was not supplied",
            f"This bill carries only lab tests and no medicines, so {count} prescribed "
            f"medicine{'s were' if count != 1 else ' was'} NOT ASSESSED. Nothing in this "
            "report states they were dispensed correctly; they were not checked.",
        ))

    tests_unassessed = [
        f for f in result.findings
        if f.rule_code == "TEST_NOT_BILLED"
        and isinstance(f.detail.get("softened_because"), str)
        and "only medicines" in str(f.detail.get("softened_because"))
    ]
    if tests_unassessed:
        count = len(tests_unassessed)
        gaps.append((
            "The lab bill was not supplied",
            f"This bill carries only medicines and no lab lines, so {count} ordered "
            f"test{'s were' if count != 1 else ' was'} NOT ASSESSED.",
        ))

    orders_unreadable = any(
        f.rule_code == "CHECK_UNAVAILABLE"
        and any("readable list of ordered investigations" in m for m in f.detail.get("missing", []))
        for f in result.findings
    )
    if orders_unreadable:
        gaps.append((
            "The investigations ordered could not be read",
            "The prescription has an investigations section that could not be read, so "
            "what was ordered is unknown. Billed tests are neither confirmed as ordered "
            "nor reported as unordered.",
        ))
    return gaps


def discrepancies(result: ReconciliationResult) -> list[Finding]:
    rank = {"critical": 0, "warning": 1, "info": 2}
    real = [f for f in result.findings if f.severity in {"critical", "warning"}]
    return sorted(real, key=lambda f: rank[f.severity])


def unavailable(result: ReconciliationResult) -> list[Finding]:
    return [f for f in result.findings if f.rule_code == "CHECK_UNAVAILABLE"]


def canonical_by_id(result: ReconciliationResult) -> dict[str, CanonicalMatch]:
    return {c.item_id: c for c in result.canonical}
