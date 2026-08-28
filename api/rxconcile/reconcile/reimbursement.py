"""Which billed lines are supported by the prescription, and for how much.

**This is not an insurance determination.** Copay tiers, coverage rules, policy
limits and exclusions appear in none of the documents this system reads, so
none of them are modelled here. Inventing them would be the never-guess rule
broken at the level of money, which is the worst place to break it.

What this does compute, entirely from findings the engine already produced:

``eligible``
    Billed lines with a prescription line behind them and nothing against them.

``not_eligible``
    Billed lines the engine flagged as having no prescription behind them at
    all -- BILL_NOT_PRESCRIBED, or SCHEDULE_H_UNBACKED for a prescription-only
    medicine with nothing backing it.

``needs_review``
    Billed lines where a check could not run, or whose matched prescription
    line carries a discrepancy. Not a rejection: a statement that a human has
    to look.

Every line lands in exactly one bucket, and every bucket lists the lines that
built it, so a reviewer can see how each amount was reached.

On money that cannot be added: a bill line with no printed amount is counted in
``lines_without_amount`` and left out of the total, never treated as zero. A
total silently missing a line is worse than a total that says it is incomplete.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from rxconcile.models import (
    BilledItem,
    BilledTest,
    Finding,
    PharmacyBill,
    ReimbursementCategory,
    ReimbursementLine,
    ReimbursementSummary,
)

#: A billed line carrying one of these has no prescription behind it.
NOT_ELIGIBLE_CODES: Final[frozenset[str]] = frozenset(
    {"BILL_NOT_PRESCRIBED", "SCHEDULE_H_UNBACKED", "TEST_NOT_PRESCRIBED"}
)

#: A billed line carrying one of these could not be fully checked.
UNCHECKED_CODES: Final[frozenset[str]] = frozenset(
    {
        "CHECK_UNAVAILABLE",
        "QUANTITY_AMBIGUOUS",
        "STRENGTH_UNIT_UNSTATED",
        "TEST_UNRESOLVED",
    }
)


def _reason(category: ReimbursementCategory, codes: list[str]) -> str:
    if category == "not_eligible":
        if "SCHEDULE_H_UNBACKED" in codes:
            return "Prescription-only medicine with nothing on the prescription behind it"
        if "TEST_NOT_PRESCRIBED" in codes:
            return "Test billed with no matching investigation ordered"
        return "Billed with no matching line on the prescription"
    if category == "needs_review":
        if any(code in UNCHECKED_CODES for code in codes):
            return "A check on this line could not be completed"
        return "The prescription line it matches carries a discrepancy"
    return "Matched to a prescribed line with nothing against it"


def assess(
    bill: PharmacyBill,
    findings: list[Finding],
    *,
    matched_billed_ids: set[str],
) -> ReimbursementSummary:
    """Sort every billed line into one of three buckets.

    Args:
        bill: the extracted bill.
        findings: every finding the engine produced.
        matched_billed_ids: billed ids the engine paired to a prescribed line,
            medicines and tests alike.
    """
    by_ref: dict[str, list[Finding]] = {}
    for finding in findings:
        if finding.billed_ref is not None:
            by_ref.setdefault(finding.billed_ref, []).append(finding)

    lines: list[ReimbursementLine] = []
    billed_lines: list[BilledItem | BilledTest] = [*bill.items, *bill.tests]
    for item in billed_lines:
        found = by_ref.get(item.item_id, [])
        codes = [f.rule_code for f in found]

        # Precedence: nothing behind it beats could-not-check beats matched.
        # A line the engine says was never prescribed is not "needs review"
        # merely because some other check on it also failed to run.
        if any(code in NOT_ELIGIBLE_CODES for code in codes):
            category: ReimbursementCategory = "not_eligible"
        elif any(code in UNCHECKED_CODES for code in codes) or any(
            f.severity in {"critical", "warning"} for f in found
        ):
            category = "needs_review"
        elif item.item_id in matched_billed_ids:
            category = "eligible"
        else:
            # Billed, unflagged and unpaired. The engine did not conclude
            # anything about it, so neither does this.
            category = "needs_review"

        name = getattr(item, "drug_name", None) or getattr(item, "test_name", None)
        lines.append(
            ReimbursementLine(
                item_id=item.item_id,
                description=name or item.raw_text,
                amount=item.line_total,
                category=category,
                reason=_reason(category, codes),
                rule_codes=sorted(set(codes)),
            )
        )

    def total(category: ReimbursementCategory) -> tuple[Decimal, int, int]:
        chosen = [line for line in lines if line.category == category]
        amounts = [line.amount for line in chosen if line.amount is not None]
        missing = sum(1 for line in chosen if line.amount is None)
        return sum(amounts, Decimal("0")), len(chosen), missing

    eligible, eligible_n, eligible_missing = total("eligible")
    not_eligible, not_eligible_n, not_eligible_missing = total("not_eligible")
    review, review_n, review_missing = total("needs_review")

    return ReimbursementSummary(
        eligible_total=eligible,
        eligible_line_count=eligible_n,
        not_eligible_total=not_eligible,
        not_eligible_line_count=not_eligible_n,
        needs_review_total=review,
        needs_review_line_count=review_n,
        lines_without_amount=eligible_missing + not_eligible_missing + review_missing,
        currency=bill.currency,
        lines=lines,
    )
