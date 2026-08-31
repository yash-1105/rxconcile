"""Does the bill add up?

Deterministic Python over values already extracted as ``Decimal``. No model is
involved in the judgement, and nothing here is computed unless every input it
needs was actually printed: **a bill with no unit price cannot fail an
arithmetic check.** Where an input is missing, the check reports that it could
not run rather than passing quietly.

Three things generate false positives on real Indian invoices, and each is
handled rather than tolerated:

**Discounts.** A discounted line legitimately breaks quantity x rate. Where the
bill prints a discount, it is subtracted before comparing. Where it does not but
the shortfall has the shape of a discount -- the line is *cheaper* than the
arithmetic, by a plausible margin -- nothing is emitted, because an
undocumented discount and a billing error are indistinguishable from the page,
and only one of them is worth accusing a pharmacy of.

**Rounding.** Bills round to the rupee constantly. A tolerance of 0.05 per line
and 1.00 on totals absorbs that without hiding a real discrepancy.

**Inclusive GST.** Many bills print rates inclusive of tax, so subtotal plus tax
exceeds the grand total by roughly the tax. That is a different printing
convention, not an error, and it is detected and skipped.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from rxconcile.models import BilledItem, BilledTest, Finding, PharmacyBill
from rxconcile.reconcile._findings import finding, unavailable

#: Per-line tolerance. Absorbs rupee rounding; far below any pack-size factor.
LINE_TOLERANCE: Final[Decimal] = Decimal("0.05")

#: Whole-bill tolerance. Totals accumulate per-line rounding.
TOTAL_TOLERANCE: Final[Decimal] = Decimal("1.00")

#: A shortfall larger than this fraction of the computed amount is too deep to
#: pass off as an unprinted discount, and is reported.
MAX_IMPLIED_DISCOUNT: Final[Decimal] = Decimal("0.30")


def _close(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    return abs(left - right) <= tolerance


def _billed_lines(bill: PharmacyBill) -> list[BilledItem | BilledTest]:
    """Every charged line, medicines and lab tests alike.

    A bill's subtotal covers both. Summing only the medicines reported a
    1080-rupee shortfall on a bill whose lab section was simply not counted --
    a false accusation manufactured by looking at half the document.
    """
    return [*bill.items, *bill.tests]


def _name(line: BilledItem | BilledTest) -> str:
    return (
        getattr(line, "drug_name", None) or getattr(line, "test_name", None) or line.raw_text
    )


def _line_findings(bill: PharmacyBill) -> list[Finding]:
    findings: list[Finding] = []
    unpriced: list[str] = []

    for item in _billed_lines(bill):
        if item.quantity is None or item.unit_price is None or item.line_total is None:
            unpriced.append(item.item_id)
            continue

        # Quantised for comparison and for the message: 10.0 x 2.20 is 22.00,
        # not 22.000, and a report should not print trailing noise.
        gross = (Decimal(str(item.quantity)) * item.unit_price).quantize(Decimal("0.01"))
        discount = getattr(item, "discount", None) or Decimal("0")
        expected = gross - discount
        if _close(expected, item.line_total, LINE_TOLERANCE):
            continue

        overcharge = item.line_total - expected
        discount_field = getattr(item, "discount", None)
        if overcharge < 0:
            # The line is CHEAPER than the arithmetic. An unprinted discount and
            # a keying error look identical here, so only an implausibly deep
            # shortfall is reported.
            shortfall = -overcharge
            if gross > 0 and shortfall / gross <= MAX_IMPLIED_DISCOUNT and discount_field is None:
                continue

        findings.append(
            finding(
                "LINE_TOTAL_MISMATCH", "warning",
                f"{_name(item)}: "
                f"{item.quantity:g} x {item.unit_price} "
                + (f"less {discount} " if discount else "")
                + f"comes to {expected}, but the line is charged {item.line_total}.",
                billed_ref=item.item_id,
                detail={
                    "quantity": float(item.quantity),
                    "unit_price": str(item.unit_price),
                    "discount": str(discount_field) if discount_field is not None else None,
                    "expected": str(expected),
                    "charged": str(item.line_total),
                    "difference": str(item.line_total - expected),
                    "tolerance": str(LINE_TOLERANCE),
                },
            )
        )

    if unpriced:
        # Reported for the DOCUMENT, not against each line. Attaching it per
        # line would push every line on a bill that omits unit prices -- which
        # is most bills -- into "needs a manual check", drowning the lines that
        # genuinely need one.
        findings.append(
            unavailable(
                "line arithmetic",
                ["a quantity, a unit price and a line total on every line"],
                note=f"{len(unpriced)} billed line(s) do not print all three, so their "
                     "arithmetic could not be checked.",
            )
        )
    return findings


def _subtotal_findings(bill: PharmacyBill) -> list[Finding]:
    lines = _billed_lines(bill)
    priced = [line.line_total for line in lines if line.line_total is not None]
    if bill.subtotal is None or not priced or len(priced) != len(lines):
        return [
            unavailable(
                "subtotal",
                ["a printed subtotal"] if bill.subtotal is None else ["a total on every line"],
                note="The line totals could not be added up and compared.",
            )
        ]

    summed = sum(priced, Decimal("0"))
    discount = bill.discount_total or Decimal("0")
    expected = summed - discount
    if _close(expected, bill.subtotal, TOTAL_TOLERANCE):
        return []

    # A subtotal below the line sum, with no discount printed, USED TO BE
    # swallowed as an unitemised discount. That silently accepted any subtotal
    # error up to 30% of the bill -- a 190-rupee gap passed unreported. A bill
    # has a place to print a discount total; where it did not, the difference is
    # reported and the possible explanation is offered in the wording rather
    # than assumed.
    undocumented_discount = (
        bill.subtotal < expected and bill.discount_total is None and summed > 0
    )
    explanation = (
        " No discount is printed on the bill; an unitemised discount would explain it."
        if undocumented_discount
        else ""
    )

    return [
        finding(
            "SUBTOTAL_MISMATCH", "warning",
            f"The {len(priced)} line totals come to {expected}"
            + (f" after a {discount} discount" if discount else "")
            + f", but the subtotal is printed as {bill.subtotal}." + explanation,
            detail={
                "line_total_sum": str(summed),
                "discount_total": str(bill.discount_total) if bill.discount_total else None,
                "expected": str(expected),
                "printed": str(bill.subtotal),
                "difference": str(bill.subtotal - expected),
                "tolerance": str(TOTAL_TOLERANCE),
                "possible_unitemised_discount": undocumented_discount,
            },
        )
    ]


def _tax_is_inclusive(bill: PharmacyBill) -> bool:
    """Whether the rates look tax-inclusive rather than tax-exclusive.

    On an inclusive bill the grand total is roughly the subtotal, and adding tax
    on top overshoots it by about the tax. Detecting that is the difference
    between reporting a printing convention and reporting an error.
    """
    if bill.subtotal is None or bill.tax_total is None or bill.grand_total is None:
        return False
    if bill.tax_total <= 0:
        return False
    return _close(bill.subtotal, bill.grand_total, TOTAL_TOLERANCE)


def _grand_total_findings(bill: PharmacyBill) -> list[Finding]:
    missing = [
        label
        for label, value in (
            ("a printed subtotal", bill.subtotal),
            ("a printed tax total", bill.tax_total),
            ("a printed grand total", bill.grand_total),
        )
        if value is None
    ]
    if missing:
        return [
            unavailable(
                "grand total",
                missing,
                note="Subtotal plus tax could not be compared against the amount payable.",
            )
        ]

    assert bill.subtotal is not None and bill.tax_total is not None
    assert bill.grand_total is not None

    if _tax_is_inclusive(bill):
        return [
            finding(
                "TAX_INCLUSIVE_PRICING", "info",
                "The printed rates appear to include tax: the grand total matches the "
                "subtotal, so the tax was not added on top. The grand-total check was "
                "skipped rather than reported as a mismatch.",
                detail={
                    "subtotal": str(bill.subtotal),
                    "tax_total": str(bill.tax_total),
                    "grand_total": str(bill.grand_total),
                },
            )
        ]

    expected = bill.subtotal + bill.tax_total
    if _close(expected, bill.grand_total, TOTAL_TOLERANCE):
        return []
    return [
        finding(
            "GRAND_TOTAL_MISMATCH", "warning",
            f"Subtotal {bill.subtotal} plus tax {bill.tax_total} comes to {expected}, "
            f"but the amount payable is printed as {bill.grand_total}.",
            detail={
                "subtotal": str(bill.subtotal),
                "tax_total": str(bill.tax_total),
                "expected": str(expected),
                "printed": str(bill.grand_total),
                "difference": str(bill.grand_total - expected),
                "tolerance": str(TOTAL_TOLERANCE),
            },
        )
    ]


def check_arithmetic(bill: PharmacyBill) -> list[Finding]:
    """Every arithmetic check, in line-then-subtotal-then-grand-total order."""
    if not bill.items:
        return []
    return [
        *_line_findings(bill),
        *_subtotal_findings(bill),
        *_grand_total_findings(bill),
    ]
