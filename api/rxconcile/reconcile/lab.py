"""Lab test reconciliation rules.

Deterministic Python, no LLM calls, exactly as for the medicine rules. This
module answers one question: were the investigations that were ordered the
investigations that were billed?

The awkward part is that the two documents describe lab work at different
granularities. A doctor writes ``LFT``; the laboratory bills SGPT, SGOT,
Bilirubin and Alkaline Phosphatase on four lines. Compared literally that reads
as one test never performed plus four never ordered -- five findings against a
bill that is, in fact, correct. So comparison happens at the level of PANEL
COMPONENTS, not written lines, via :mod:`rxconcile.normalize.lab_panels`.

Two failure modes are designed against explicitly, because both turn one
unreadable line into confident accusations:

**An unreadable orders section is not an empty one.** If the prescription has an
investigations block that could not be read, every billed test would otherwise
look unordered. Absent and unreadable are different results and only one of them
is clean, so an unconfirmed orders section softens every accusation that depends
on having read it.

**An unresolved panel decomposes to nothing, not to zero components.** If
``lab_panels`` cannot resolve what the doctor wrote, its component set is empty
-- and an empty set trivially covers nothing, which would report every billed
component as unprescribed. That is one illegible word becoming four criticals.
An unresolved order therefore suppresses confident accusations rather than
licensing them, the same shape as the unidentifiable-drug fix on the medicine
side.
"""

from __future__ import annotations

from typing import Final

from rxconcile.models import (
    BilledTest,
    Finding,
    MatchedPair,
    PharmacyBill,
    PrescribedTest,
    Prescription,
    Submission,
)
from rxconcile.normalize import lab_panels
from rxconcile.reconcile._findings import finding, unavailable


class LabOutcome:
    """Findings plus the pairing lists, mirroring the medicine step's shape."""

    __slots__ = (
        "covered", "findings", "matched", "unmatched_prescribed", "unmatched_billed",
    )

    def __init__(
        self,
        findings: list[Finding],
        matched: list[MatchedPair],
        unmatched_prescribed: list[str],
        unmatched_billed: list[str],
        covered: set[str] | None = None,
    ) -> None:
        self.findings = findings
        self.matched = matched
        self.unmatched_prescribed = unmatched_prescribed
        self.unmatched_billed = unmatched_billed
        #: EVERY billed line a matched panel accounted for, not only the primary
        #: one named in `matched`. An ordered LFT billed as seven analytes pairs
        #: to one of them; the other six are just as much accounted for, and
        #: anything downstream that only reads `matched` will treat them as
        #: unexplained.
        self.covered = covered or set()


#: Similarity recorded on a test pair. Panel matching is set membership rather
#: than a graded score, so a full cover is 1.0 and a partial cover is the
#: fraction of components actually billed.
_FULL: Final[float] = 1.0


def _written(test: PrescribedTest | BilledTest) -> str:
    """What the page says, preferring the parsed name but never losing the line."""
    return test.test_name or test.raw_text


def _resolve(test: PrescribedTest | BilledTest) -> lab_panels.LabMatch:
    return lab_panels.resolve(_written(test))


def _orders_uncertain(
    prescription: Prescription, unresolved_orders: int
) -> tuple[str, str] | None:
    """Why billed-but-not-ordered accusations cannot be made confidently, if so.

    Returns ``(code, reason)`` or None. The CODE is what callers branch on: a
    reader once saw "no lab bill supplied" on a bill carrying five lab lines,
    because the UI matched on prose and the prose had two possible meanings.
    """
    if unresolved_orders:
        return (
            "unidentified_orders",
            f"{unresolved_orders} ordered investigation(s) on the prescription could "
            "not be identified, so this test may well have been among them",
        )
    if prescription.investigations_present and not prescription.tests:
        return (
            "orders_unreadable",
            "the prescription has an investigations section that could not be read, "
            "so what was ordered is unknown",
        )
    if prescription.investigations_present is None and not prescription.tests:
        return (
            "orders_unconfirmed",
            "no investigations section was found on the prescription, but its "
            "presence could not be confirmed either",
        )
    return None


def _components(match: lab_panels.LabMatch) -> tuple[str, ...]:
    return match.components


def reconcile_tests(
    prescription: Prescription,
    bill: PharmacyBill,
    submission: Submission | None = None,
) -> LabOutcome:
    """Compare ordered investigations against billed lab lines.

    A document with no tests on either side produces no findings at all. The
    absence of lab work is not a discrepancy and must not render as one.

    ``submission`` says which documents the operator uploaded. When it is
    present, a missing lab bill is read from THAT rather than inferred from
    whether the extracted bill happened to carry lab lines -- which is what
    once put "no lab bill supplied" against a bill holding five of them.
    """
    rx_tests = list(prescription.tests)
    bill_tests = list(bill.tests)

    if not rx_tests and not bill_tests and not prescription.investigations_present:
        return LabOutcome([], [], [], [], set())

    findings: list[Finding] = []
    matched: list[MatchedPair] = []

    rx_matches = {test.item_id: _resolve(test) for test in rx_tests}
    bill_matches = {billed.item_id: _resolve(billed) for billed in bill_tests}

    # Component -> billed line ids offering it. A bill that lists the panel
    # wholesale offers every component from that single line.
    offered: dict[str, list[str]] = {}
    for billed in bill_tests:
        for component in _components(bill_matches[billed.item_id]):
            offered.setdefault(component, []).append(billed.item_id)

    unresolved_orders = [test for test in rx_tests if not rx_matches[test.item_id].resolved]
    unresolved_bills = [
        billed for billed in bill_tests if not bill_matches[billed.item_id].resolved
    ]

    # The mirror of the lab-only-bill guard on the medicine side.
    #
    # With a submission, this is a FACT the operator stated: no lab bill was
    # uploaded. Without one, fall back to the old inference so a direct caller
    # of the engine still behaves sensibly.
    if submission is not None:
        no_lab_bill = not submission.lab_bill_supplied
    else:
        no_lab_bill = bool(bill.items) and not bill_tests

    consumed: set[str] = set()
    unmatched_rx: list[str] = []

    # ---- ordered tests -------------------------------------------------
    for test in rx_tests:
        match = rx_matches[test.item_id]

        if not match.resolved:
            # Never expand to zero components and treat that as "covers
            # nothing". Say the line could not be read and stop.
            unmatched_rx.append(test.item_id)
            findings.append(
                finding(
                    "TEST_UNRESOLVED", "info",
                    f"{_written(test)!r} on the prescription could not be identified as "
                    "a known test or panel, so it could not be checked against the bill.",
                    prescribed_ref=test.item_id,
                    detail={"written": _written(test), "side": "prescription"},
                )
            )
            findings.append(
                unavailable(
                    "test billing",
                    ["a recognised test or panel name"],
                    prescribed_ref=test.item_id,
                    note="An unidentified order is not evidence that nothing was "
                         "ordered, and nothing on the bill is judged against it.",
                )
            )
            continue

        components = _components(match)
        covering = [cid for component in components for cid in offered.get(component, [])]
        billed_components = [c for c in components if offered.get(c)]
        # Derived analytes are calculated by the laboratory, not billed, so
        # their absence is not a shortfall. See lab_panels.DERIVED_COMPONENTS.
        required = (
            lab_panels.required_components(match.name)
            if match.kind == "panel" and match.name
            else components
        )
        missing = [c for c in required if not offered.get(c)]

        if not billed_components:
            uncertain: str | None = None
            uncertain_code: str | None = None
            if no_lab_bill:
                uncertain_code = "no_lab_bill"
                uncertain = (
                    "no lab bill was uploaded, so there is nothing to check this "
                    "against"
                )
            elif unresolved_bills:
                uncertain_code = "unidentified_billed_lines"
                uncertain = (
                    f"{len(unresolved_bills)} billed lab line(s) could not be identified, "
                    "so one of them may be this test"
                )
            unmatched_rx.append(test.item_id)
            findings.append(
                finding(
                    "TEST_NOT_BILLED",
                    "warning" if uncertain else "critical",
                    f"{match.name} was ordered but does not appear on the bill"
                    + (f" -- though {uncertain}." if uncertain else "."),
                    prescribed_ref=test.item_id,
                    detail={
                        "written": _written(test),
                        "resolved_as": match.name,
                        "kind": match.kind,
                        "components": list(components),
                        "softened_because": uncertain,
                        "softened_code": uncertain_code,
                    },
                )
            )
            continue

        consumed.update(covering)
        primary = covering[0]
        coverage = len(billed_components) / len(required) if required else 1.0
        matched.append(
            MatchedPair(
                prescribed_id=test.item_id,
                billed_id=primary,
                similarity=_FULL if not missing else round(coverage, 4),
            )
        )

        if missing:
            findings.append(
                finding(
                    "PANEL_PARTIAL", "warning",
                    f"{match.name} was ordered as a panel but the bill covers only "
                    f"{len(billed_components)} of its {len(required)} billable components. "
                    f"Not billed: {', '.join(missing)}.",
                    prescribed_ref=test.item_id,
                    billed_ref=primary,
                    detail={
                        "panel": match.name,
                        "billed_components": billed_components,
                        "missing_components": missing,
                        "coverage": round(coverage, 4),
                    },
                )
            )

    # ---- billed lines nothing accounted for -----------------------------
    orders_doubt = _orders_uncertain(prescription, len(unresolved_orders))
    orders_code = orders_doubt[0] if orders_doubt else None
    uncertain_orders = orders_doubt[1] if orders_doubt else None
    unmatched_bill: list[str] = []
    for billed in bill_tests:
        if billed.item_id in consumed:
            continue
        unmatched_bill.append(billed.item_id)
        match = bill_matches[billed.item_id]

        if not match.resolved:
            findings.append(
                finding(
                    "TEST_UNRESOLVED", "info",
                    f"{_written(billed)!r} on the bill could not be identified as a "
                    "known test or panel, so it could not be checked against the "
                    "prescription.",
                    billed_ref=billed.item_id,
                    detail={"written": _written(billed), "side": "bill"},
                )
            )
            findings.append(
                unavailable(
                    "test authorisation",
                    ["a recognised test or panel name"],
                    billed_ref=billed.item_id,
                    note="This line is neither confirmed as ordered nor reported as "
                         "unordered; a reviewer must read it.",
                )
            )
            continue

        findings.append(
            finding(
                "TEST_NOT_PRESCRIBED",
                "warning" if uncertain_orders else "critical",
                f"{match.name} was billed but does not appear among the ordered "
                "investigations"
                + (f" -- though {uncertain_orders}." if uncertain_orders else "."),
                billed_ref=billed.item_id,
                detail={
                    "written": _written(billed),
                    "resolved_as": match.name,
                    "softened_because": uncertain_orders,
                    "softened_code": orders_code,
                },
            )
        )

    if uncertain_orders and bill_tests:
        findings.append(
            unavailable(
                "test authorisation",
                ["a readable list of ordered investigations"],
                note=uncertain_orders.capitalize() + ".",
            )
        )

    # ---- repeat billing --------------------------------------------------
    # Two shapes of the same abuse: one test on two lines, or one line with a
    # quantity above one. The second was a silent skip until the null audit.
    unquantified: list[str] = []
    for billed in bill_tests:
        if billed.quantity is None:
            unquantified.append(billed.item_id)
            continue
        if billed.quantity > 1:
            name = bill_matches[billed.item_id].name or _written(billed)
            findings.append(
                finding(
                    "TEST_DUPLICATE", "warning",
                    f"{name} is billed with a quantity of "
                    f"{billed.quantity:g}. A test is normally performed once.",
                    billed_ref=billed.item_id,
                    detail={"test": name, "quantity": billed.quantity},
                )
            )
    if unquantified:
        findings.append(
            unavailable(
                "repeat test billing",
                ["a quantity"],
                billed_ref=unquantified[0] if len(unquantified) == 1 else None,
                note=f"{len(unquantified)} billed lab line(s) state no quantity, so "
                     "whether any was charged more than once could not be checked.",
            )
        )

    seen: dict[str, str] = {}
    for billed in bill_tests:
        match = bill_matches[billed.item_id]
        if match.name is None:
            continue
        first = seen.get(match.name)
        if first is None:
            seen[match.name] = billed.item_id
            continue
        findings.append(
            finding(
                "TEST_DUPLICATE", "warning",
                f"{match.name} is billed more than once.",
                billed_ref=billed.item_id,
                detail={"test": match.name, "first_billed_ref": first},
            )
        )

    return LabOutcome(findings, matched, unmatched_rx, unmatched_bill, consumed)
