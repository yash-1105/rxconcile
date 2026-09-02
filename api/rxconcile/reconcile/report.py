"""The report axis of the three-way comparison.

The prescription ORDERS a test, the lab bill CHARGES for it, the report PROVES
it was performed. Two of those three could already be compared; this adds the
third, which is the only one that can answer "charged for, but never done".

**Nothing here reads a result.** The rules compare which tests appear on which
document -- names, not numbers. A value of 33.16 against a range of 75-250 is
irrelevant to every rule in this file, and hard rule 10 forbids it being
otherwise. That is what makes report comparison document reconciliation rather
than clinical judgement.

Panel decomposition is `lab_panels.resolve`, the same helper the ordered/billed
axis uses. A second path would drift, and drift here means the same test
resolving differently depending on which document named it.
"""

from __future__ import annotations

from typing import Final, NamedTuple

from rxconcile.models import (
    Finding,
    LabReport,
    PharmacyBill,
    Prescription,
)
from rxconcile.normalize import lab_panels
from rxconcile.reconcile._findings import finding, unavailable

#: Days a collection date may precede the prescription before it is remarked on.
#: Not zero: a sample taken the same morning the prescription was written is
#: ordinary, and clocks on two documents rarely agree to the hour.
COLLECTED_BEFORE_GRACE_DAYS: Final[int] = 1


class ReportOutcome(NamedTuple):
    findings: list[Finding]
    #: Canonical names proved performed. Empty when no report was supplied,
    #: which is not the same as a report proving nothing.
    reported: set[str]


def _canonical(names: list[str | None]) -> dict[str, set[str]]:
    """Written name -> the canonical component names it resolves to."""
    out: dict[str, set[str]] = {}
    for name in names:
        match = lab_panels.resolve(name)
        if match.resolved and match.components:
            out.setdefault(name or "", set()).update(match.components)
    return out


def _components(names: list[str | None]) -> set[str]:
    covered: set[str] = set()
    for expansion in _canonical(names).values():
        covered |= expansion
    return covered


def reconcile_report(
    prescription: Prescription,
    bill: PharmacyBill,
    report: LabReport | None,
    *,
    lab_bill_supplied: bool,
) -> ReportOutcome:
    """Compare a lab report against what was ordered and what was billed.

    A missing report is **not applicable**, never a gap. Nobody is obliged to
    upload one, and reporting its absence as a shortfall would invent a
    requirement the product does not have. So an absent report produces no
    findings at all -- not a softened warning, not an unavailable check about
    the report itself.
    """
    if report is None:
        return ReportOutcome([], set())

    findings: list[Finding] = []
    reported_names = [test.test_name for test in report.tests]
    reported = _components(reported_names)
    ordered = _components([test.test_name for test in prescription.tests])
    billed = _components([test.test_name for test in bill.tests])

    unresolved_reported = [
        test for test in report.tests
        if test.test_name is not None and not lab_panels.resolve(test.test_name).resolved
    ]

    # --- billed but never reported -------------------------------------
    #
    # The one rule the report axis exists for: a charge with no result behind
    # it. Critical, because it is the shape of billing for work not done.
    if not lab_bill_supplied and not billed:
        # No charges to check against. Recorded rather than skipped silently.
        findings.append(
            unavailable(
                "billed against reported",
                ["a lab bill"],
                note="No lab bill was uploaded, so no charge could be checked against "
                     "the report. The report itself was read.",
            )
        )
    else:
        for name in sorted(billed - reported):
            findings.append(
                finding(
                    "TEST_BILLED_NOT_REPORTED", "critical",
                    f"{name} was billed but no result for it appears on the lab report.",
                    detail={
                        "test": name,
                        "reported_tests": sorted(reported),
                        "unresolved_reported_lines": len(unresolved_reported),
                    },
                )
            )

    # --- reported but never billed -------------------------------------
    #
    # Info, not a problem: a laboratory routinely reports more than a bill
    # itemises, and a package billed as one line covers several results.
    if lab_bill_supplied or billed:
        for name in sorted(reported - billed):
            findings.append(
                finding(
                    "TEST_REPORTED_NOT_BILLED", "info",
                    f"{name} has a result on the report but no charge on the bill.",
                    detail={"test": name},
                )
            )

    # --- reported but never ordered ------------------------------------
    #
    # Warning. A result nobody asked for is worth a reviewer's eye, but people
    # legitimately add tests, and a prescription whose investigations section
    # could not be read must not turn every result into a finding.
    orders_readable = bool(ordered) or prescription.investigations_present is False
    if orders_readable:
        for name in sorted(reported - ordered):
            findings.append(
                finding(
                    "TEST_REPORTED_NOT_ORDERED", "warning",
                    f"{name} was performed and reported but does not appear on the "
                    "prescription.",
                    detail={"test": name, "ordered_tests": sorted(ordered)},
                )
            )
    elif reported:
        findings.append(
            unavailable(
                "reported against ordered",
                ["a readable investigations section"],
                note="Nothing could be read from the prescription's investigations "
                     "section, so whether these results were ordered is unknown.",
            )
        )

    # --- patient identity ----------------------------------------------
    if report.patient_name and prescription.patient_name:
        if not _same_person(report.patient_name, prescription.patient_name):
            findings.append(
                finding(
                    "REPORT_PATIENT_MISMATCH", "warning",
                    f"The lab report names {report.patient_name!r} but the prescription "
                    f"names {prescription.patient_name!r}.",
                    detail={
                        "report": report.patient_name,
                        "prescription": prescription.patient_name,
                    },
                )
            )

    # --- collected before it was ordered --------------------------------
    if report.collected_date is not None and prescription.date_issued is not None:
        days = (prescription.date_issued - report.collected_date).days
        if days > COLLECTED_BEFORE_GRACE_DAYS:
            findings.append(
                finding(
                    "REPORT_DATE_ANOMALY", "warning",
                    f"The sample was collected on {report.collected_date.isoformat()}, "
                    f"{days} day(s) before the prescription was written on "
                    f"{prescription.date_issued.isoformat()}.",
                    detail={
                        "collected": report.collected_date.isoformat(),
                        "prescription_date": prescription.date_issued.isoformat(),
                        "days_before": days,
                        "grace_days": COLLECTED_BEFORE_GRACE_DAYS,
                    },
                )
            )
    elif report.collected_date is None or prescription.date_issued is None:
        missing = []
        if report.collected_date is None:
            missing.append("a collection date on the report")
        if prescription.date_issued is None:
            missing.append("a date on the prescription")
        findings.append(
            unavailable(
                "report timing",
                missing,
                note="Without both dates there is nothing to measure the sample "
                     "against, so the timing was neither cleared nor flagged.",
            )
        )

    if unresolved_reported:
        findings.append(
            unavailable(
                "reported tests",
                ["a recognisable test name"],
                note=f"{len(unresolved_reported)} result line(s) name a test this build "
                     "does not recognise, so they were compared against nothing. This "
                     "says the NAME was not looked up, not that the page was unreadable.",
            )
        )

    return ReportOutcome(findings, reported)


def _same_person(left: str, right: str) -> bool:
    """Whether two written names plausibly denote the same person.

    Deliberately loose. Titles, initials, middle names and transliteration all
    vary between a handwritten prescription and a printed report -- "Mr. ISHAN
    ROY" and "Ishan Ray" are the same man. The rule exists to catch somebody
    else's report attached to this claim, not to police spelling.
    """
    def tokens(name: str) -> set[str]:
        cleaned = name.lower().replace(".", " ")
        drop = {"mr", "mrs", "ms", "miss", "dr", "master", "baby", "b/o", "c/o"}
        return {t for t in cleaned.split() if t and t not in drop}

    a, b = tokens(left), tokens(right)
    if not a or not b:
        return True  # Nothing to compare is not a mismatch.
    return bool(a & b)
