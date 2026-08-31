"""History-based checks: duplicate bills, early repeats, licence consistency.

The load-bearing tests are the ones about restraint: a corrected re-issue must
not be reported as a duplicate, thin history must not read as a clean result,
and a missing course length must never be replaced with an assumed one.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from rxconcile.models import (
    BilledItem,
    CanonicalMatch,
    Finding,
    PharmacyBill,
    PrescribedItem,
    Prescription,
)
from rxconcile.models.schema import CHECK_UNAVAILABLE_CODE
from rxconcile.reconcile.history import (
    MIN_MEANINGFUL_HISTORY,
    HistoryScope,
    PriorCourse,
    PriorLine,
    PriorScan,
    check_history,
)

TODAY = dt.date(2026, 8, 23)
PHARMACY = "SRI BALAJI MEDICALS & DIAGNOSTICS"


def bill(
    *,
    bill_no: str | None = "8842",
    pharmacy: str | None = PHARMACY,
    licence: str | None = "TN/2019/337821",
    date: dt.date | None = TODAY,
    patient: str | None = "Anil Deshmukh",
    lines: tuple[tuple[str, str], ...] = (("TELMA", "267.00"), ("GLYCOMET", "192.00")),
    grand_total: str | None = "459.00",
) -> PharmacyBill:
    return PharmacyBill(
        currency="INR", pharmacy_name=pharmacy, pharmacy_licence_no=licence,
        bill_no=bill_no, bill_date=date, patient_name=patient,
        grand_total=Decimal(grand_total) if grand_total else None,
        items=[
            BilledItem(item_id=f"bill-{i:02d}", raw_text=name, drug_name=name,
                       line_total=Decimal(total), confidence=0.9)
            for i, (name, total) in enumerate(lines, start=1)
        ],
    )


def prescription(*salts: str) -> Prescription:
    return Prescription(
        overall_legibility=0.93,
        items=[
            PrescribedItem(item_id=f"rx-{i:02d}", raw_text=s, drug_name=s, confidence=0.9)
            for i, s in enumerate(salts, start=1)
        ],
    )


def canonical(*salts: str) -> list[CanonicalMatch]:
    return [
        CanonicalMatch(item_id=f"rx-{i:02d}", side="prescription", name=s, salt=s,
                       match_score=100.0, method="exact")
        for i, s in enumerate(salts, start=1)
    ]


def prior(
    scan_id: int = 1,
    *,
    days_ago: int = 9,
    bill_no: str | None = "8842",
    pharmacy: str | None = PHARMACY,
    licence: str | None = "TN/2019/337821",
    patient: str | None = "Anil Deshmukh",
    lines: tuple[tuple[str, str], ...] = (("TELMA", "267.00"), ("GLYCOMET", "192.00")),
    grand_total: str | None = "459.00",
    courses: tuple[tuple[str, int | None], ...] = (),
) -> PriorScan:
    return PriorScan(
        scan_id=scan_id,
        created_at=dt.datetime.combine(TODAY - dt.timedelta(days=days_ago), dt.time(10, 0)),
        employee_name="Yash",
        pharmacy_name=pharmacy, pharmacy_licence_no=licence, bill_no=bill_no,
        bill_date=TODAY - dt.timedelta(days=days_ago), patient_name=patient,
        grand_total=Decimal(grand_total) if grand_total else None,
        lines=tuple(PriorLine(name=n, line_total=Decimal(t)) for n, t in lines),
        courses=tuple(PriorCourse(salt=s, duration_days=d) for s, d in courses),
    )


def padding(count: int) -> list[PriorScan]:
    """Unrelated scans, so a lack of duplicates is meaningful."""
    return [
        prior(scan_id=100 + i, bill_no=f"P{i}", pharmacy=f"Other Pharmacy {i}",
              licence=f"XX/{i}", patient=f"Someone {i}",
              lines=(("SOMETHING", "10.00"),))
        for i in range(count)
    ]


def scope(n: int, *, admin: bool = False) -> HistoryScope:
    return HistoryScope(scans_compared=n, role="admin" if admin else "employee",
                        limited_to_own_scans=not admin)


def run(
    priors: list[PriorScan], *, rx_salts: tuple[str, ...] = (), **kwargs: object
) -> list[Finding]:
    b = bill(**kwargs)  # type: ignore[arg-type]
    return check_history(
        prescription(*rx_salts), b, canonical(*rx_salts), priors, scope(len(priors))
    )


def codes(findings: list[Finding], code: str) -> list[str]:
    return [f.severity for f in findings if f.rule_code == code]


# ---------------------------------------------------------------------------
# No history at all
# ---------------------------------------------------------------------------


def test_the_first_ever_scan_reports_that_nothing_could_be_compared() -> None:
    """A first scan is not a clean history. It is no history."""
    found = check_history(prescription(), bill(), [], [], scope(0))
    assert len(found) == 1
    assert found[0].rule_code == CHECK_UNAVAILABLE_CODE
    assert "first scan on record" in found[0].detail["note"]
    assert not codes(found, "DUPLICATE_BILL")


# ---------------------------------------------------------------------------
# Duplicate bill
# ---------------------------------------------------------------------------


def test_an_identical_bill_is_a_critical_duplicate() -> None:
    found = run([prior(scan_id=7), *padding(MIN_MEANINGFUL_HISTORY)])
    assert codes(found, "DUPLICATE_BILL") == ["critical"]
    detail = next(f.detail for f in found if f.rule_code == "DUPLICATE_BILL")
    assert detail["prior_scan_id"] == 7
    assert detail["prior_scan_date"], "a reviewer must be able to open the earlier scan"


def test_a_different_pharmacy_with_the_same_number_is_not_a_duplicate() -> None:
    found = run([prior(pharmacy="Apollo Pharmacy"), *padding(MIN_MEANINGFUL_HISTORY)])
    assert not codes(found, "DUPLICATE_BILL")


def test_without_a_bill_number_it_falls_back_to_pharmacy_date_and_lines() -> None:
    found = run(
        [prior(bill_no=None, days_ago=0), *padding(MIN_MEANINGFUL_HISTORY)],
        bill_no=None,
    )
    assert codes(found, "DUPLICATE_BILL") == ["critical"]
    detail = next(f.detail for f in found if f.rule_code == "DUPLICATE_BILL")
    assert detail["matched_on"] == "pharmacy, date and overlapping line items"


def test_the_fallback_needs_the_lines_to_overlap() -> None:
    found = run(
        [prior(bill_no=None, days_ago=0, lines=(("SOMETHING ELSE", "5.00"),)),
         *padding(MIN_MEANINGFUL_HISTORY)],
        bill_no=None,
    )
    assert not codes(found, "DUPLICATE_BILL")


# ---------------------------------------------------------------------------
# The one that matters: a correction is not fraud
# ---------------------------------------------------------------------------


def test_a_bill_with_a_changed_total_is_a_resubmission_not_a_duplicate() -> None:
    found = run(
        [prior(scan_id=4, grand_total="612.00"), *padding(MIN_MEANINGFUL_HISTORY)]
    )
    assert codes(found, "POSSIBLE_RESUBMISSION") == ["warning"]
    assert not codes(found, "DUPLICATE_BILL"), "an honest correction is not fraud"
    detail = next(f.detail for f in found if f.rule_code == "POSSIBLE_RESUBMISSION")
    assert detail["prior_scan_id"] == 4
    assert any("612.00" in d for d in detail["differences"])


def test_a_removed_line_is_named_in_the_differences() -> None:
    found = run([
        prior(lines=(("TELMA", "267.00"), ("GLYCOMET", "192.00"), ("ZINCOVIT", "64.50"))),
        *padding(MIN_MEANINGFUL_HISTORY),
    ])
    detail = next(f.detail for f in found if f.rule_code == "POSSIBLE_RESUBMISSION")
    assert any("ZINCOVIT" in d for d in detail["differences"])


def test_a_changed_line_price_is_named() -> None:
    found = run([
        prior(lines=(("TELMA", "300.00"), ("GLYCOMET", "192.00"))),
        *padding(MIN_MEANINGFUL_HISTORY),
    ])
    detail = next(f.detail for f in found if f.rule_code == "POSSIBLE_RESUBMISSION")
    assert any("TELMA" in d and "300.00" in d for d in detail["differences"])


# ---------------------------------------------------------------------------
# Thin history
# ---------------------------------------------------------------------------


def test_thin_history_says_so_rather_than_asserting_a_clean_result() -> None:
    found = run([prior(bill_no="9999", pharmacy="Somewhere Else")])
    duplicate_checks = [
        f for f in found
        if f.rule_code == CHECK_UNAVAILABLE_CODE and f.detail.get("check") == "duplicate bill"
    ]
    assert duplicate_checks, "one prior scan proves almost nothing"
    assert "too little history" in duplicate_checks[0].detail["note"]


def test_enough_history_and_no_match_stays_silent() -> None:
    found = run(padding(MIN_MEANINGFUL_HISTORY + 2))
    assert not [
        f for f in found
        if f.rule_code == CHECK_UNAVAILABLE_CODE and f.detail.get("check") == "duplicate bill"
    ]


# ---------------------------------------------------------------------------
# Early repeat
# ---------------------------------------------------------------------------


def repeat_prior(**kwargs: object) -> PriorScan:
    """An earlier visit: same patient, different bill."""
    defaults: dict[str, object] = {
        "bill_no": "5501", "pharmacy": "MEDPLUS, ADYAR", "licence": "TN/2020/551200",
        "lines": (("TELMA 20", "267.00"),),
    }
    defaults.update(kwargs)
    return prior(**defaults)  # type: ignore[arg-type]


def test_a_repeat_inside_the_previous_course_is_a_warning() -> None:
    found = run(
        [repeat_prior(scan_id=3, days_ago=9, courses=(("Telmisartan", 30),))],
        rx_salts=("Telmisartan",),
    )
    assert codes(found, "EARLY_REPEAT") == ["warning"]
    detail = next(f.detail for f in found if f.rule_code == "EARLY_REPEAT")
    assert detail["days_since_previous"] == 9
    assert detail["previous_course_days"] == 30


def test_a_repeat_after_the_course_has_run_out_is_not_flagged() -> None:
    found = run(
        [repeat_prior(days_ago=40, courses=(("Telmisartan", 30),))],
        rx_salts=("Telmisartan",),
    )
    assert not codes(found, "EARLY_REPEAT")


def test_matching_is_on_salt_so_a_brand_switch_does_not_slip_through() -> None:
    """Dolo then Calpol is the same medicine claimed twice."""
    found = run(
        [repeat_prior(days_ago=3, courses=(("Paracetamol", 10),))],
        rx_salts=("Paracetamol",),
    )
    assert codes(found, "EARLY_REPEAT") == ["warning"]


def test_a_different_patient_is_not_a_repeat() -> None:
    found = run(
        [repeat_prior(days_ago=3, patient="Someone Else", courses=(("Telmisartan", 30),))],
        rx_salts=("Telmisartan",),
    )
    assert not codes(found, "EARLY_REPEAT")


def test_a_missing_course_length_is_reported_never_assumed() -> None:
    """Substituting a default course is the fabricated-duration problem again."""
    found = run(
        [repeat_prior(days_ago=3, courses=(("Telmisartan", None),))],
        rx_salts=("Telmisartan",),
    )
    assert not codes(found, "EARLY_REPEAT")
    checks = [
        f for f in found
        if f.rule_code == CHECK_UNAVAILABLE_CODE and f.detail.get("check") == "early repeat"
    ]
    assert checks
    assert "No default course was assumed" in checks[0].detail["note"]


def test_no_bill_date_means_no_repeat_interval_can_be_measured() -> None:
    found = run(
        [repeat_prior(days_ago=3, courses=(("Telmisartan", 30),))],
        rx_salts=("Telmisartan",), date=None,
    )
    assert not codes(found, "EARLY_REPEAT")
    assert [
        f for f in found
        if f.rule_code == CHECK_UNAVAILABLE_CODE and f.detail.get("check") == "early repeat"
    ]


# ---------------------------------------------------------------------------
# Licence consistency
# ---------------------------------------------------------------------------


def test_two_licence_numbers_for_one_pharmacy_is_a_warning() -> None:
    found = run([prior(scan_id=5, licence="TN/2016/884413", bill_no="6104")])
    assert codes(found, "LICENCE_INCONSISTENT") == ["warning"]
    detail = next(f.detail for f in found if f.rule_code == "LICENCE_INCONSISTENT")
    assert 5 in detail["conflicting_scan_ids"]
    assert len(detail["licence_numbers"]) == 2


def test_one_consistent_licence_raises_nothing() -> None:
    found = run([prior(scan_id=5, bill_no="6104")] + padding(MIN_MEANINGFUL_HISTORY))
    assert not codes(found, "LICENCE_INCONSISTENT")


def test_a_different_pharmacy_is_not_a_licence_conflict() -> None:
    found = run(
        [prior(pharmacy="Apollo Pharmacy", licence="TN/2016/884413", bill_no="6104")]
        + padding(MIN_MEANINGFUL_HISTORY)
    )
    assert not codes(found, "LICENCE_INCONSISTENT")


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("admin", [False, True])
def test_every_history_finding_states_what_was_searched(admin: bool) -> None:
    """A report must not imply the whole record was searched when it was not."""
    priors = [prior(scan_id=7)]
    found = check_history(
        prescription(), bill(), [], priors, scope(len(priors), admin=admin)
    )
    for item in found:
        note = item.detail.get("history_scope") or item.detail.get("note") or ""
        assert note, f"{item.rule_code} does not say what it compared against"
        if not admin:
            assert "other accounts were not searched" in note


def test_a_bill_is_never_a_repeat_of_itself() -> None:
    """The duplicate check already says so; "claimed 0 days ago" is noise."""
    same = prior(scan_id=3, days_ago=0, courses=(("Telmisartan", 30),))
    found = run([same], rx_salts=("Telmisartan",))
    assert codes(found, "DUPLICATE_BILL") or codes(found, "POSSIBLE_RESUBMISSION")
    assert not codes(found, "EARLY_REPEAT")


def test_one_finding_per_salt_against_the_most_recent_claim() -> None:
    found = run(
        [
            repeat_prior(scan_id=3, days_ago=9, courses=(("Telmisartan", 30),)),
            repeat_prior(scan_id=4, days_ago=20, bill_no="4400",
                         courses=(("Telmisartan", 30),)),
        ],
        rx_salts=("Telmisartan",),
    )
    assert codes(found, "EARLY_REPEAT") == ["warning"], "one row per medicine"
    detail = next(f.detail for f in found if f.rule_code == "EARLY_REPEAT")
    assert detail["days_since_previous"] == 9, "the most recent claim is the relevant one"
