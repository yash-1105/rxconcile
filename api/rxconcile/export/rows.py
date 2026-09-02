"""The rows an export renders, mirroring what the screen renders.

`web/src/lib/rows.ts` and `web/src/lib/rowStatus.ts` decide what a table row is
and what state it is in. A report that derived its own answer would disagree
with the screen it claims to be a record of, so this module mirrors them
deliberately and `tests/test_export_rows.py` asserts the two agree.

Nothing here re-decides how serious a finding is. Severity comes from the
engine; this only decides which of the engine's verdicts leads.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from rxconcile.models import (
    BilledItem,
    BilledTest,
    Finding,
    PrescribedItem,
    PrescribedTest,
    ReconciliationResult,
)

RowState = Literal[
    "clean", "substitution", "warning", "problem", "unchecked", "out-of-scope"
]

#: Findings that say a check was attempted or skipped, never that something is
#: wrong. Mirrors UNCHECKED_CODES in web/src/lib/rowStatus.ts.
UNCHECKED_CODES: Final[frozenset[str]] = frozenset(
    {"QUANTITY_AMBIGUOUS", "STRENGTH_UNIT_UNSTATED", "TEST_UNRESOLVED", "CHECK_UNAVAILABLE"}
)

#: The words the screen uses. Upper-cased for a table cell, so a report survives
#: greyscale: the tint is a convenience, the word is the statement.
STATUS_LABEL: Final[dict[RowState, str]] = {
    "clean": "MATCHES",
    "substitution": "SUBSTITUTED",
    "warning": "CHECK",
    "problem": "PROBLEM",
    "unchecked": "NOT CHECKED",
    "out-of-scope": "OUT OF SCOPE",
}

#: Row tints, the same values as `--color-tint-*` in web/src/index.css.
TINT: Final[dict[RowState, str]] = {
    "clean": "#e2f0e8",
    "substitution": "#fbefd6",
    "warning": "#fdeae4",
    "problem": "#f8d9d3",
    "unchecked": "#ecefec",
    "out-of-scope": "#ecefec",
}


def test_label(test: PrescribedTest | BilledTest | None) -> str | None:
    """What to show for a test line. Mirrors `testLabel` in web/src/lib/rows.ts.

    `test_name` is null whenever the extractor read the line but could not
    isolate a name from it. `raw_text` is never nulled, so it is always the
    honest fallback -- without it a perfectly readable line renders as an
    em-dash in every column.
    """
    if test is None:
        return None
    name = (test.test_name or "").strip()
    if name:
        return name
    return (test.raw_text or "").strip() or None


def status_of(findings: list[Finding], *, paired: bool) -> tuple[RowState, bool]:
    """A row's state and whether a check on it could not be concluded.

    Explicit precedence, mirroring `statusFrom`: critical, then warning, then a
    confirmed non-medicine, then the pairing itself. A check that could not run
    is a marker beside the row, never a downgrade of it.
    """
    partial = any(f.rule_code in UNCHECKED_CODES for f in findings)
    if any(f.severity == "critical" for f in findings):
        return "problem", partial
    if any(f.severity == "warning" for f in findings):
        return "warning", partial
    # Read, understood, and simply not a medicine. Neither a problem nor a line
    # nobody managed to check -- which is what the reports used to call it,
    # printing MATCHES against a body lotion.
    if any(f.rule_code == "NON_MEDICINE_ITEM" for f in findings):
        return "out-of-scope", False
    if paired:
        # A clean pair where the brand differs is a substitution, not a plain
        # match. Mirrors `withSubstitution`.
        if any(f.rule_code == "BRAND_SUBSTITUTION" for f in findings):
            return "substitution", partial
        return "clean", partial
    return "unchecked", False


class MedicineRow(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    key: str
    prescribed: PrescribedItem | None = None
    billed: BilledItem | None = None
    similarity: float | None = None
    findings: list[Finding] = []
    state: RowState = "unchecked"
    partial: bool = False


class TestRow(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    key: str
    prescribed: PrescribedTest | None = None
    billed: BilledTest | None = None
    findings: list[Finding] = []
    state: RowState = "unchecked"
    partial: bool = False
    #: The ordered panel this billed line was counted against, when it is one of
    #: several covering a single order. Stated by the engine, never guessed.
    covered_by: str | None = None
    #: How many billed lines cover this ordered test.
    covers_count: int = 0


def _for_pair(findings: list[Finding], prescribed_id: str, billed_id: str) -> list[Finding]:
    """Findings belonging to a matched row.

    A finding may name both halves or only one -- EXPIRED_ITEM names the billed
    line alone. It belongs here when every ref it carries points at this row and
    it carries at least one. Mirrors `findingsForPair`.
    """
    kept: list[Finding] = []
    for f in findings:
        if f.prescribed_ref is None and f.billed_ref is None:
            continue
        if f.prescribed_ref is not None and f.prescribed_ref != prescribed_id:
            continue
        if f.billed_ref is not None and f.billed_ref != billed_id:
            continue
        kept.append(f)
    return kept


def medicine_rows(result: ReconciliationResult) -> list[MedicineRow]:
    rx = {i.item_id: i for i in result.prescription.items}
    bill = {i.item_id: i for i in result.bill.items}
    rows: list[MedicineRow] = []

    def build(
        key: str,
        prescribed: PrescribedItem | None,
        billed: BilledItem | None,
        similarity: float | None,
        found: list[Finding],
    ) -> MedicineRow:
        state, partial = status_of(found, paired=prescribed is not None and billed is not None)
        return MedicineRow(
            key=key, prescribed=prescribed, billed=billed, similarity=similarity,
            findings=found, state=state, partial=partial,
        )

    for pair in result.matched_pairs:
        rows.append(
            build(
                f"{pair.prescribed_id}-{pair.billed_id}",
                rx.get(pair.prescribed_id),
                bill.get(pair.billed_id),
                pair.similarity,
                _for_pair(result.findings, pair.prescribed_id, pair.billed_id),
            )
        )
    for item_id in result.unmatched_prescribed:
        found = [f for f in result.findings if f.prescribed_ref == item_id]
        rows.append(build(f"rx-only-{item_id}", rx.get(item_id), None, None, found))
    for item_id in result.unmatched_billed:
        found = [f for f in result.findings if f.billed_ref == item_id]
        rows.append(build(f"bill-only-{item_id}", None, bill.get(item_id), None, found))
    return rows


def test_rows(result: ReconciliationResult) -> list[TestRow]:
    rx = {t.item_id: t for t in result.prescription.tests}
    bill = {t.item_id: t for t in result.bill.tests}
    rows: list[TestRow] = []

    for pair in result.matched_tests:
        found = _for_pair(result.findings, pair.prescribed_id, pair.billed_id)
        state, partial = status_of(
            found, paired=pair.prescribed_id in rx and pair.billed_id in bill
        )
        rows.append(
            TestRow(
                key=f"{pair.prescribed_id}-{pair.billed_id}",
                prescribed=rx.get(pair.prescribed_id), billed=bill.get(pair.billed_id),
                findings=found, state=state, partial=partial,
                covers_count=len(pair.covers),
            )
        )

    # A panel match consumes every billed line that covered it, but only the
    # primary appears in matched_tests. Without these the table would drop them,
    # and a report that does not account for every line on the bill is worse
    # than no report.
    covering: dict[str, str] = {}
    for pair in result.matched_tests:
        ordered = rx.get(pair.prescribed_id)
        name = test_label(ordered)
        for billed_id in pair.covers:
            if name:
                covering[billed_id] = name
    accounted = {p.billed_id for p in result.matched_tests} | set(
        result.unmatched_billed_tests
    )
    for test in result.bill.tests:
        if test.item_id in accounted:
            continue
        rows.append(
            TestRow(
                key=f"covered-{test.item_id}", prescribed=None, billed=test,
                findings=[], state="clean", partial=False,
                covered_by=covering.get(test.item_id),
            )
        )

    for item_id in result.unmatched_prescribed_tests:
        found = [f for f in result.findings if f.prescribed_ref == item_id]
        state, partial = status_of(found, paired=False)
        rows.append(
            TestRow(key=f"rxt-{item_id}", prescribed=rx.get(item_id), findings=found,
                    state=state, partial=partial)
        )
    for item_id in result.unmatched_billed_tests:
        found = [f for f in result.findings if f.billed_ref == item_id]
        state, partial = status_of(found, paired=False)
        rows.append(
            TestRow(key=f"bt-{item_id}", billed=bill.get(item_id), findings=found,
                    state=state, partial=partial)
        )
    return rows


class Counts(BaseModel):
    matched: int = 0
    problems: int = 0


def counts(states: list[RowState]) -> Counts:
    """The two figures the summary shows. Mirrors `countRows`.

    `unchecked` and `out-of-scope` are in neither bucket on purpose: neither is
    a match, and neither is a problem with the bill.
    """
    return Counts(
        matched=sum(1 for s in states if s in {"clean", "substitution"}),
        problems=sum(1 for s in states if s in {"problem", "warning"}),
    )


def claimable_amount(row: MedicineRow | TestRow) -> Decimal:
    """What this line contributes when accepted. Mirrors `claimableAmount`."""
    if row.state == "out-of-scope" or row.billed is None:
        return Decimal("0")
    total = row.billed.line_total
    if total is None:
        return Decimal("0")
    paired = row.prescribed is not None
    panel_covered = row.state == "clean" and not row.findings
    return total if (paired or panel_covered) else Decimal("0")


def panel_of(row: TestRow) -> str | None:
    for found in row.findings:
        value = found.detail.get("panel") or found.detail.get("resolved_as")
        if isinstance(value, str) and value:
            return value
    for side in (row.prescribed, row.billed):
        if side is not None and getattr(side, "panel", None):
            return str(side.panel)
    return None


def lab_remark(row: TestRow, remark: Callable[[list[Finding]], str]) -> str:
    """What a lab row says. Mirrors `labRemark` on the screen."""
    if row.findings:
        return remark(row.findings)
    if row.prescribed is None and row.billed is not None:
        return (f"Billed as part of {row.covered_by}" if row.covered_by
                else "Billed as part of an ordered panel")
    if row.prescribed is not None and row.covers_count > 1:
        return f"Ordered as a panel — billed as {row.covers_count} itemised lines"
    if row.prescribed is not None and row.billed is not None:
        return "Ordered and billed"
    return remark(row.findings)
