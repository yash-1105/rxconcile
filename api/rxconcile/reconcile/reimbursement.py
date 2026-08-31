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

#: A line that is simply not a medicine. Its own quiet category: folding it into
#: "not on prescription" would read as an accusation about a delivery charge.
NON_MEDICINE_CODE: Final[str] = "NON_MEDICINE_ITEM"

#: Codes that outrank "not a medicine" when bucketing. A delivery charge is
#: never on the prescription, so BILL_NOT_PRESCRIBED is deliberately absent
#: here -- if it outranked, the non-medicine category would never be reached.
#: The FINDING is untouched either way: only the money bucket changes.
OVERRIDES_NON_MEDICINE: Final[frozenset[str]] = frozenset(
    {"SCHEDULE_H_UNBACKED", "TEST_NOT_PRESCRIBED"}
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


#: Plain wording for why a line landed where it did, most specific first.
#:
#: One definition, used by the screen and by every export, so a reader is never
#: told two different things about the same line. Written for someone with no
#: knowledge of the rule codes underneath.
_REASONS: Final[tuple[tuple[str, str], ...]] = (
    ("SCHEDULE_H_UNBACKED", "Prescription-only medicine with nothing on the prescription"),
    ("TEST_NOT_PRESCRIBED", "This test was not ordered on the prescription"),
    ("BILL_NOT_PRESCRIBED", "Nothing on the prescription matches this item"),
    ("STRENGTH_MISMATCH", "Strength differs from the prescription"),
    ("SALT_DIFFERENT_CLASS", "A different kind of medicine to the one prescribed"),
    ("FORM_MISMATCH", "Dispensed in a different form to the one prescribed"),
    ("QUANTITY_SHORT", "Less was dispensed than the course requires"),
    ("QUANTITY_EXCESS", "More was dispensed than the course requires"),
    ("DUPLICATE_THERAPY", "The same medicine appears on more than one line"),
    ("TEST_DUPLICATE", "This test is billed more than once"),
    ("PANEL_PARTIAL", "Only part of the ordered panel was billed"),
    (
        "QUANTITY_AMBIGUOUS",
        "Quantity could not be confirmed — the bill does not say whether it counts "
        "packs or tablets",
    ),
    ("STRENGTH_UNIT_UNSTATED", "Strength was not printed on one of the documents"),
    ("TEST_UNRESOLVED", "This is not a test name the system recognises"),
    ("CHECK_UNAVAILABLE", "One of the checks on this line could not be completed"),
)


def _reason(category: ReimbursementCategory, codes: list[str]) -> str:
    if category == "non_medicine":
        return "Not a medicine — usually outside reimbursement"
    for code, wording in _REASONS:
        if code in codes:
            return wording
    if category == "not_eligible":
        return "Nothing on the prescription matches this item"
    if category == "needs_review":
        return "This line could not be fully checked"
    return "Matches the prescription"


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
        if NON_MEDICINE_CODE in codes and not any(
            code in OVERRIDES_NON_MEDICINE for code in codes
        ):
            # Outside reimbursement scope rather than unsupported by the
            # prescription. Never allowed to hide a real finding: a line that
            # is ALSO unprescribed keeps that, harsher, category.
            category: ReimbursementCategory = "non_medicine"
        elif any(code in NOT_ELIGIBLE_CODES for code in codes):
            category = "not_eligible"
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

    non_medicine, non_medicine_n, non_medicine_missing = total("non_medicine")
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
        non_medicine_total=non_medicine,
        non_medicine_line_count=non_medicine_n,
        lines_without_amount=(
            eligible_missing + not_eligible_missing + review_missing + non_medicine_missing
        ),
        currency=bill.currency,
        lines=lines,
    )
