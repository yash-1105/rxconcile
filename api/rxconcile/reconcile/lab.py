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


def _identified(test: PrescribedTest | BilledTest) -> bool:
    """Whether we know WHICH test this line is -- enough to rule it out.

    Named `_legible` once, which was wrong twice over. It does not measure
    whether the page was read, and it is not the lookup either. It answers one
    question: can this line be ruled out as a counterpart for a test we cannot
    find?

    * "~~ smudge ~~" with no name -- could be anything, including the missing
      CBC. Not ruled out, so an accusation about the CBC is softened.
    * "Vitamin D (25-OH)" -- a name we read perfectly and simply do not hold in
      lab_panels. A known, DIFFERENT test, so it cannot be the missing one and
      nothing is softened on its account.

    A parsed name is what separates those, which is why this tests `test_name`.
    Nothing here knows WHY a name is absent -- a poor photograph and a string
    that would not parse look identical from here. So findings built on this
    say "could not be identified", never "could not be read": the second picks
    one cause, and picks the one that blames the submitter's photograph.
    """
    return bool(test.test_name)


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
    # Only a line whose TEXT could not be read is a possible counterpart for an
    # ordered test that is missing. A legible line naming a different test is
    # not a candidate: "Vitamin D (25-OH)" cannot be the missing KFT, and
    # counting it softened two genuinely unbilled tests into warnings under the
    # wording "some billed lab lines could not be read", which was false about
    # a bill every line of which had been read perfectly.
    unreadable_bills = [billed for billed in bill_tests if not _identified(billed)]

    # Whether an ordered test CAN be checked is a property of the documents, not
    # of which upload slot a file was dropped into.
    #
    # Reading `submission.lab_bill_supplied` alone was wrong: an Indian pharmacy
    # that is also a diagnostics centre bills medicines and lab work on ONE
    # document, so no separate lab bill is uploaded and lab lines arrive on the
    # pharmacy bill anyway. That softened two genuinely unbilled tests into
    # warnings and reported them as "not assessed" when they had been assessed
    # against five billed lab lines sitting right there.
    #
    # So the evidence decides whether to soften, and the submission only
    # explains WHY there is none -- which is the part that must be stated
    # rather than inferred.
    nothing_to_check_against = not bill_tests
    if submission is not None:
        no_lab_bill = nothing_to_check_against and not submission.lab_bill_supplied
        # A lab bill that WAS uploaded and carries no readable test line is a
        # different statement, and a worse one: the document is there and could
        # not be read. "No lab bill was supplied" about it would be false.
        lab_bill_unreadable = nothing_to_check_against and submission.lab_bill_supplied
    else:
        # No submission to state it, so infer -- but only from evidence that a
        # separate lab bill plausibly exists. A bill carrying medicines and no
        # lab lines is a pharmacy bill. An EMPTY bill is evidence of nothing,
        # and softening on it would excuse every ordered test on the strength
        # of a document nobody supplied.
        no_lab_bill = bool(bill.items) and nothing_to_check_against
        lab_bill_unreadable = False

    consumed: set[str] = set()
    unmatched_rx: list[str] = []

    # ---- ordered tests -------------------------------------------------
    for test in rx_tests:
        match = rx_matches[test.item_id]

        if not match.resolved:
            # Never expand to zero components and treat that as "covers
            # nothing". Say the line was not RECOGNISED -- not that it could not
            # be read, which is a different failure with a different remedy: a
            # sharper photograph fixes one, and only a better dictionary fixes
            # the other.
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
            elif lab_bill_unreadable:
                uncertain_code = "lab_bill_unreadable"
                uncertain = (
                    "a lab bill was uploaded but no test line on it could be read, "
                    "so there is nothing to check this against"
                )
            elif unreadable_bills:
                uncertain_code = "unidentified_billed_lines"
                # "could not be IDENTIFIED", not "could not be read". A line may
                # be unidentified because the photograph was poor or because the
                # text would not parse into a name, and this code cannot tell
                # which. Saying "could not be read" picks one and blames the
                # submitter's photograph for what may be our own parse.
                uncertain = (
                    f"{len(unreadable_bills)} billed lab line(s) could not be "
                    "identified, so one of them may be this test"
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
                covers=list(covering),
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

        # A dictionary miss is still recorded, because a gap in our reference
        # data is worth knowing about -- but it no longer stops the check.
        if not match.resolved:
            findings.append(
                finding(
                    "TEST_UNRESOLVED", "info",
                    f"{_written(billed)!r} on the bill is not a test this build "
                    "recognises, so it could not be matched to an ordered panel.",
                    billed_ref=billed.item_id,
                    detail={
                        "written": _written(billed),
                        "side": "bill",
                        "legible": _identified(billed),
                    },
                )
            )

        # Reported whether or not the dictionary knew it. Resolution is needed to
        # MATCH a test to an ordered panel; it is not needed to observe that a
        # line appears on the bill and not on the prescription. Skipping this
        # meant a legible, unordered test was silently never reported.
        if not _identified(billed):
            # Nothing was read off this line, so nothing can be said about it.
            findings.append(
                unavailable(
                    "test authorisation",
                    ["a readable test name on the billed line"],
                    billed_ref=billed.item_id,
                    note="This line is neither confirmed as ordered nor reported as "
                         "unordered; a reviewer must read it.",
                )
            )
            continue

        name = match.name if match.resolved else _written(billed)
        findings.append(
            finding(
                "TEST_NOT_PRESCRIBED",
                "warning" if uncertain_orders else "critical",
                f"{name} was billed but does not appear among the ordered "
                "investigations"
                + (f" -- though {uncertain_orders}." if uncertain_orders else "."),
                billed_ref=billed.item_id,
                detail={
                    "written": _written(billed),
                    "resolved_as": name,
                    "identified": match.resolved,
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
