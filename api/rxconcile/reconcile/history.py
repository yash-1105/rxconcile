"""Checks that compare this bill against scans already on record.

Deterministic Python, and **no database access**: prior scans arrive as plain
data that the API layer has already loaded and already narrowed to what the
signed-in account may see. The engine stays pure and testable, and a history
check cannot accidentally widen its own visibility.

Two properties matter more than the checks themselves.

**A corrected resubmission is not fraud.** The same pharmacy re-issuing a bill
with a fixed line looks, on a bill number, exactly like someone claiming twice.
Where the earlier scan and this one differ, this reports a possible
resubmission and names the differences, rather than an accusation.

**Thin history is not a clean result.** A duplicate check against one prior scan
proves almost nothing. Where there is too little history for silence to mean
anything, that is said, not implied.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict
from rapidfuzz import fuzz

from rxconcile.models import (
    BilledItem,
    BilledTest,
    CanonicalMatch,
    Finding,
    PharmacyBill,
    Prescription,
)
from rxconcile.reconcile._findings import finding, unavailable

#: Two pharmacy names above this are treated as the same pharmacy.
PHARMACY_MATCH_SCORE: Final[float] = 85.0

#: Two patient names above this are treated as the same person.
PATIENT_MATCH_SCORE: Final[float] = 85.0

#: Share of line items in common before two bills without bill numbers are
#: considered the same document.
LINE_OVERLAP_THRESHOLD: Final[float] = 0.80

#: Below this many prior scans, an absence of duplicates says nothing, and the
#: report says so rather than reading as a pass.
MIN_MEANINGFUL_HISTORY: Final[int] = 5


class PriorLine(BaseModel):
    """One billed line on a scan already on record."""

    model_config = ConfigDict(frozen=True)

    name: str = ""
    line_total: Decimal | None = None


class PriorCourse(BaseModel):
    """A salt previously claimed, and the course length that was prescribed.

    ``duration_days`` is null far more often than not -- it is only populated
    when the sig parser resolved a duration without assuming one.
    """

    model_config = ConfigDict(frozen=True)

    salt: str
    duration_days: int | None = None


class PriorScan(BaseModel):
    """A scan already on record, reduced to what the history checks read."""

    model_config = ConfigDict(frozen=True)

    scan_id: int
    created_at: dt.datetime
    employee_name: str = ""
    pharmacy_name: str | None = None
    pharmacy_licence_no: str | None = None
    bill_no: str | None = None
    bill_date: dt.date | None = None
    patient_name: str | None = None
    grand_total: Decimal | None = None
    lines: tuple[PriorLine, ...] = ()
    courses: tuple[PriorCourse, ...] = ()


class HistoryScope(BaseModel):
    """What the signed-in account was able to compare against.

    Carried into every finding's detail: an employee's duplicate check reads
    only their own scans, and a report must not imply the whole record was
    searched.
    """

    model_config = ConfigDict(frozen=True)

    scans_compared: int = 0
    role: str = "employee"
    limited_to_own_scans: bool = True

    @property
    def note(self) -> str:
        if self.limited_to_own_scans:
            return (
                f"Compared against the {self.scans_compared} scan(s) on this account. "
                "Scans filed by other accounts were not searched."
            )
        return f"Compared against all {self.scans_compared} scan(s) on record."


def _key(value: str | None) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _same_pharmacy(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return fuzz.token_sort_ratio(left.lower(), right.lower()) >= PHARMACY_MATCH_SCORE


def _same_patient(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return fuzz.token_sort_ratio(left.lower(), right.lower()) >= PATIENT_MATCH_SCORE


def _lines_of(bill: PharmacyBill) -> tuple[PriorLine, ...]:
    lines: list[BilledItem | BilledTest] = [*bill.items, *bill.tests]
    return tuple(
        PriorLine(
            name=(
                getattr(line, "drug_name", None)
                or getattr(line, "test_name", None)
                or line.raw_text
            ),
            line_total=line.line_total,
        )
        for line in lines
    )


def _overlap(left: tuple[PriorLine, ...], right: tuple[PriorLine, ...]) -> float:
    """Share of line names in common, over the larger of the two."""
    a = {_key(line.name) for line in left if _key(line.name)}
    b = {_key(line.name) for line in right if _key(line.name)}
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


def _differences(bill: PharmacyBill, prior: PriorScan) -> list[str]:
    """What changed between the earlier bill and this one, in plain words."""
    current = _lines_of(bill)
    differences: list[str] = []

    if len(current) != len(prior.lines):
        differences.append(
            f"{len(prior.lines)} line(s) before, {len(current)} now"
        )

    before = {_key(line.name): line for line in prior.lines}
    now = {_key(line.name): line for line in current}
    added = [now[k].name for k in now.keys() - before.keys()]
    removed = [before[k].name for k in before.keys() - now.keys()]
    if added:
        differences.append(f"added: {', '.join(sorted(added))}")
    if removed:
        differences.append(f"no longer billed: {', '.join(sorted(removed))}")

    for key in before.keys() & now.keys():
        was, is_now = before[key].line_total, now[key].line_total
        if was is not None and is_now is not None and was != is_now:
            differences.append(f"{now[key].name}: {was} before, {is_now} now")

    if (
        bill.grand_total is not None
        and prior.grand_total is not None
        and bill.grand_total != prior.grand_total
    ):
        differences.append(f"total {prior.grand_total} before, {bill.grand_total} now")

    return differences


def _duplicate_findings(
    bill: PharmacyBill, priors: list[PriorScan], scope: HistoryScope
) -> tuple[list[Finding], frozenset[int]]:
    """Findings, and the ids of prior scans identified as THIS bill.

    The second value keeps the repeat check from reporting a bill as a repeat
    of itself.
    """
    matches: list[PriorScan] = []
    method = ""

    if bill.bill_no:
        matches = [
            prior for prior in priors
            if prior.bill_no and _key(prior.bill_no) == _key(bill.bill_no)
            and _same_pharmacy(prior.pharmacy_name, bill.pharmacy_name)
        ]
        method = "bill number and pharmacy name"
    if not matches:
        # No bill number, or none matched: fall back to the same pharmacy on the
        # same day billing substantially the same lines.
        matches = [
            prior for prior in priors
            if _same_pharmacy(prior.pharmacy_name, bill.pharmacy_name)
            and prior.bill_date is not None
            and prior.bill_date == bill.bill_date
            and _overlap(_lines_of(bill), prior.lines) >= LINE_OVERLAP_THRESHOLD
        ]
        method = "pharmacy, date and overlapping line items"

    if not matches:
        if len(priors) < MIN_MEANINGFUL_HISTORY:
            # Silence here would read as "checked, not a duplicate". Against
            # this little history it means almost nothing.
            return (
                [
                    unavailable(
                        "duplicate bill",
                        [f"more than {len(priors)} prior scan(s) to compare against"],
                        note="No matching bill was found, but there is too little "
                             f"history for that to mean much. {scope.note}",
                    )
                ],
                frozenset(),
            )
        return [], frozenset()

    prior = min(matches, key=lambda p: p.created_at)
    differences = _differences(bill, prior)
    shared = {
        "prior_scan_id": prior.scan_id,
        "prior_scan_date": prior.created_at.date().isoformat(),
        "prior_employee": prior.employee_name,
        "prior_bill_no": prior.bill_no,
        "matched_on": method,
        "history_scope": scope.note,
        "other_matches": [p.scan_id for p in matches if p.scan_id != prior.scan_id],
    }

    same_bill = frozenset(match.scan_id for match in matches)
    if differences:
        # A corrected resubmission looks exactly like a duplicate on a bill
        # number. Reporting an honest correction as fraud is the worse error.
        return [
            finding(
                "POSSIBLE_RESUBMISSION", "warning",
                f"A bill with these details was already recorded as scan #{prior.scan_id} "
                f"on {prior.created_at.date().isoformat()}, but this one differs: "
                f"{'; '.join(differences[:3])}. That is consistent with a corrected "
                "re-issue rather than a repeat claim.",
                detail={**shared, "differences": differences},
            )
        ], same_bill

    return [
        finding(
            "DUPLICATE_BILL", "critical",
            f"This bill appears to have been submitted before, as scan "
            f"#{prior.scan_id} on {prior.created_at.date().isoformat()}"
            + (f" by {prior.employee_name}" if prior.employee_name else "")
            + ". The line items and totals are identical.",
            detail=shared,
        )
    ], same_bill


def _repeat_findings(
    prescription: Prescription,
    bill: PharmacyBill,
    canonical: list[CanonicalMatch],
    priors: list[PriorScan],
    scope: HistoryScope,
    same_bill_ids: frozenset[int] = frozenset(),
) -> list[Finding]:
    if bill.bill_date is None:
        return [
            unavailable(
                "early repeat",
                ["a resolvable bill date"],
                note="Without a date on this bill there is nothing to measure a repeat "
                     "interval from.",
            )
        ]

    salts_now = {
        match.salt
        for match in canonical
        if match.side == "prescription" and match.salt
    }
    if not salts_now:
        return []

    findings: list[Finding] = []
    undated: list[str] = []
    # One finding per salt, against the most recent prior claim of it. Sorting
    # newest-first means the first match found is the relevant one.
    reported: set[str] = set()

    for prior in sorted(priors, key=lambda p: p.bill_date or dt.date.min, reverse=True):
        # The same document is not a repeat of itself. Where the duplicate check
        # already identified this prior as this bill, reporting every salt as
        # "claimed 0 days ago" is noise on top of an accusation already made.
        if prior.scan_id in same_bill_ids:
            continue
        if not _same_patient(prior.patient_name, bill.patient_name):
            continue
        if prior.bill_date is None:
            continue
        gap = (bill.bill_date - prior.bill_date).days
        if gap < 0:
            continue
        for course in prior.courses:
            if course.salt not in salts_now or course.salt in reported:
                continue
            if course.duration_days is None:
                # Never substitute a default course length. Fabricating a
                # duration is the problem already fixed once in extraction.
                undated.append(course.salt)
                continue
            if gap >= course.duration_days:
                continue
            reported.add(course.salt)
            findings.append(
                finding(
                    "EARLY_REPEAT", "warning",
                    f"{course.salt} was claimed {gap} day(s) ago on scan "
                    f"#{prior.scan_id}, against a {course.duration_days}-day course. "
                    "The earlier course should not have run out yet.",
                    detail={
                        "salt": course.salt,
                        "days_since_previous": gap,
                        "previous_course_days": course.duration_days,
                        "prior_scan_id": prior.scan_id,
                        "prior_bill_date": prior.bill_date.isoformat(),
                        "history_scope": scope.note,
                    },
                )
            )

    if undated:
        findings.append(
            unavailable(
                "early repeat",
                ["a course duration on the earlier prescription"],
                note=f"{len(set(undated))} previously claimed medicine(s) did not state "
                     "a course length that could be resolved, so no repeat interval "
                     "could be measured. No default course was assumed.",
            )
        )
    return findings


def _licence_findings(
    bill: PharmacyBill, priors: list[PriorScan], scope: HistoryScope
) -> list[Finding]:
    """The only real signal available for a drug licence, and only at volume."""
    same_pharmacy = [
        prior for prior in priors if _same_pharmacy(prior.pharmacy_name, bill.pharmacy_name)
    ]
    if not same_pharmacy:
        return []

    seen: dict[str, list[int]] = {}
    if bill.pharmacy_licence_no:
        seen[_key(bill.pharmacy_licence_no)] = []
    for prior in same_pharmacy:
        if prior.pharmacy_licence_no:
            seen.setdefault(_key(prior.pharmacy_licence_no), []).append(prior.scan_id)

    if len(seen) < 2:
        return []

    printed = sorted(
        {p.pharmacy_licence_no for p in same_pharmacy if p.pharmacy_licence_no}
        | ({bill.pharmacy_licence_no} if bill.pharmacy_licence_no else set())
    )
    return [
        finding(
            "LICENCE_INCONSISTENT", "warning",
            f"{bill.pharmacy_name} has appeared with {len(seen)} different drug licence "
            f"numbers across the scans on record: {', '.join(printed)}.",
            detail={
                "pharmacy": bill.pharmacy_name,
                "licence_numbers": printed,
                "conflicting_scan_ids": sorted(
                    scan_id for ids in seen.values() for scan_id in ids
                ),
                "history_scope": scope.note,
            },
        )
    ]


def check_history(
    prescription: Prescription,
    bill: PharmacyBill,
    canonical: list[CanonicalMatch],
    priors: list[PriorScan],
    scope: HistoryScope,
) -> list[Finding]:
    """Every history-based check.

    With no prior scans at all, all three report that they could not run. A
    first scan is not a clean history; it is no history.
    """
    if not priors:
        return [
            unavailable(
                "scan history",
                ["any earlier scan to compare against"],
                note="This is the first scan on record for this account, so duplicate, "
                     "repeat-purchase and licence-consistency checks had nothing to "
                     f"compare against. {scope.note}",
            )
        ]

    duplicate, same_bill = _duplicate_findings(bill, priors, scope)
    return [
        *duplicate,
        *_repeat_findings(prescription, bill, canonical, priors, scope, same_bill),
        *_licence_findings(bill, priors, scope),
    ]
