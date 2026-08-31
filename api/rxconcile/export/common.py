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

#: Plain wording, matching the screen exactly.
CATEGORY_LABEL: Final[dict[str, str]] = {
    "eligible": "Covered by prescription",
    "not_eligible": "Not on prescription",
    "needs_review": "Needs a manual check",
    "non_medicine": "Not a medicine",
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
        if f.rule_code == "TEST_NOT_BILLED" and f.detail.get("softened_code") == "no_lab_bill"
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


#: Rule codes worth a Remark, most severe first. The order IS the precedence.
#:
#: Mirrors web/src/lib/phrasing.ts so the screen and a printed report never say
#: two different things about the same row.
_REMARKS: Final[tuple[tuple[str, str], ...]] = (
    ("SALT_DIFFERENT_CLASS", "Different kind of medicine to the one prescribed"),
    ("SCHEDULE_H_UNBACKED", "Prescription-only medicine with nothing backing it"),
    ("STRENGTH_MISMATCH", "Strength differs"),
    ("BILL_NOT_PRESCRIBED", "Not on the prescription"),
    ("RX_NOT_BILLED", "Prescribed but not dispensed"),
    ("TEST_NOT_PRESCRIBED", "Not on the prescription"),
    ("TEST_NOT_BILLED", "Ordered but not done"),
    ("FORM_MISMATCH", "Dispensed in a different form"),
    ("QUANTITY_SHORT", "Less dispensed than the course requires"),
    ("QUANTITY_EXCESS", "More dispensed than the course requires"),
    ("DUPLICATE_THERAPY", "Same medicine on more than one line"),
    ("TEST_DUPLICATE", "Billed more than once"),
    ("PANEL_PARTIAL", "Only part of the ordered panel was billed"),
    ("BRAND_SUBSTITUTION", "Brand substitution"),
    ("LINE_TOTAL_MISMATCH", "Line total does not match quantity x rate"),
    ("QUANTITY_AMBIGUOUS", "Quantity could not be confirmed"),
    ("NON_MEDICINE_ITEM", "Not a medicine"),
    ("STRENGTH_UNIT_UNSTATED", "Strength not printed on one document"),
    ("TEST_UNRESOLVED", "Not a test name the system recognises"),
)


def status_word(found: list[Finding]) -> str:
    """A row's status as a WORD, never a colour.

    One definition for the screen, the PDF and the workbook. The Excel sheet
    used to derive its own and printed NOTED where the other two said MATCHES.
    """
    for severity in ("critical", "warning"):
        if any(f.severity == severity for f in found):
            return STATUS_WORD[severity]
    return "MATCHES"


def short_remark(found: list[Finding]) -> str:
    """One short sentence naming the most important thing about a row.

    Capped deliberately. Joining every message produced remarks seven lines
    deep in the PDF, stacking four statements into a cell nobody reads. The
    full detail stays in the JSON export.
    """
    codes = {f.rule_code for f in found}
    sayable = [(code, wording) for code, wording in _REMARKS if code in codes]
    if not sayable:
        return "—"
    code, wording = sayable[0]
    detail = next((f.detail for f in found if f.rule_code == code), {})

    if code == "STRENGTH_MISMATCH":
        expected, billed = detail.get("expected"), detail.get("found")
        if isinstance(expected, dict) and isinstance(billed, dict):
            wording = (
                f"Strength differs: {expected.get('value')}{expected.get('unit') or ''}"
                f" vs {billed.get('value')}{billed.get('unit') or ''}"
            )
    elif code == "BRAND_SUBSTITUTION":
        prescribed, billed_brand = detail.get("prescribed_brand"), detail.get("billed_brand")
        if prescribed and billed_brand:
            wording = f"Brand substitution — {billed_brand} for {prescribed}, same salt"
    elif code == "RX_NOT_BILLED" and detail.get("lab_only_bill") is True:
        wording = "Not assessed — no pharmacy bill supplied"
    elif code in {"BILL_NOT_PRESCRIBED", "RX_NOT_BILLED"} and detail.get("identified") is False:
        wording = "This line could not be read"

    return f"{wording} (+{len(sayable) - 1} more)" if len(sayable) > 1 else wording


def unchecked_line(result: ReconciliationResult) -> str | None:
    """One sentence replacing the internal check-name table.

    The number in the reimbursement total needs an explanation that survives
    the app: an unexplained money figure on a report is worse than a technical
    one. Naming the internal checks was not that explanation.
    """
    count = result.reimbursement.needs_review_line_count
    if not count:
        return None
    return (
        f"{count} billed line{'s' if count != 1 else ''} could not be fully checked and "
        f"need{'' if count != 1 else 's'} a manual review. The reason for each is shown "
        "in the table above."
    )


#: Never allowed to become the hidden half of a "+N more".
PINNED_CODE: Final[str] = "SCHEDULE_H_UNBACKED"

#: Tie-break within one severity: the more specific message wins. Mirrors
#: web/src/lib/grouping.ts so all three surfaces tell the same story.
_SPECIFICITY: Final[tuple[str, ...]] = (
    PINNED_CODE,
    "SALT_DIFFERENT_CLASS",
    "STRENGTH_MISMATCH",
    "DUPLICATE_THERAPY",
    "PANEL_PARTIAL",
    "TEST_DUPLICATE",
    "FORM_MISMATCH",
    "QUANTITY_SHORT",
    "QUANTITY_EXCESS",
    "BRAND_SUBSTITUTION",
    "TEST_NOT_PRESCRIBED",
    "TEST_NOT_BILLED",
    "BILL_NOT_PRESCRIBED",
    "RX_NOT_BILLED",
    "LINE_TOTAL_MISMATCH",
    "GSTIN_INVALID",
    "LICENCE_ABSENT",
    "NON_MEDICINE_ITEM",
)

_SEVERITY_RANK: Final[dict[str, int]] = {"critical": 0, "warning": 1, "info": 2}


class FindingGroup(BaseModel):
    """Every finding about one item, headline first."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    key: str
    headline: Finding
    findings: list[Finding]
    severity: str

    @property
    def extra(self) -> int:
        return len(self.findings) - 1

    @property
    def is_discrepancy(self) -> bool:
        return self.severity != "info"


def _finding_rank(finding: Finding) -> tuple[int, int]:
    try:
        specificity = _SPECIFICITY.index(finding.rule_code)
    except ValueError:
        specificity = len(_SPECIFICITY)
    return _SEVERITY_RANK[finding.severity], specificity


def group_findings(result: ReconciliationResult) -> list[FindingGroup]:
    """One row per item, the way the screen shows it.

    Alprax produced two rows -- "billed but not prescribed" and "prescription-
    only medicine with nothing backing it" -- which is one fact told twice.

    A matched pair is one item: a finding may carry a prescribed ref, a billed
    ref or both, so refs are folded together through the pairs the engine
    reported. Document-level findings concern no item and each stays its own
    row rather than being merged into something they are not about.
    """
    canonical: dict[str, str] = {}
    for pair in [*result.matched_pairs, *result.matched_tests]:
        canonical[pair.prescribed_id] = pair.prescribed_id
        canonical[pair.billed_id] = pair.prescribed_id

    buckets: dict[str, list[Finding]] = {}
    order: list[str] = []
    for index, finding in enumerate(result.findings):
        if finding.severity not in {"critical", "warning", "info"}:
            continue
        ref = finding.prescribed_ref or finding.billed_ref
        key = f"doc-{index}" if ref is None else canonical.get(ref, ref)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(finding)

    groups: list[FindingGroup] = []
    for key in order:
        members = sorted(buckets[key], key=_finding_rank)
        # Pinned, not ranked: a Schedule H finding heads its row whatever else
        # is in it. It is the most consequential thing this system detects and
        # exactly what a naive merge buries under a more generic line.
        pinned = next((f for f in members if f.rule_code == PINNED_CODE), None)
        if pinned is not None:
            members = [pinned, *[f for f in members if f is not pinned]]
        severity = min((f.severity for f in members), key=lambda s: _SEVERITY_RANK[s])
        groups.append(
            FindingGroup(key=key, headline=members[0], findings=members, severity=severity)
        )

    return sorted(groups, key=lambda g: (_SEVERITY_RANK[g.severity], _finding_rank(g.headline)))


def discrepancy_groups(result: ReconciliationResult) -> list[FindingGroup]:
    """Groups that count as a discrepancy: items with a problem, not findings."""
    return [group for group in group_findings(result) if group.is_discrepancy]


def discrepancies(result: ReconciliationResult) -> list[Finding]:
    rank = {"critical": 0, "warning": 1, "info": 2}
    real = [f for f in result.findings if f.severity in {"critical", "warning"}]
    return sorted(real, key=lambda f: rank[f.severity])


def unavailable(result: ReconciliationResult) -> list[Finding]:
    return [f for f in result.findings if f.rule_code == "CHECK_UNAVAILABLE"]


def canonical_by_id(result: ReconciliationResult) -> dict[str, CanonicalMatch]:
    return {c.item_id: c for c in result.canonical}
