"""Annual allowance, and what draws against it.

The load-bearing test here is the last one. Rejecting a line reduces the claim
for its own scan and must NOT reduce the employee's used-so-far or balance —
it is easy to get backwards, and it is the first thing anyone will check.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlmodel import Session, SQLModel, create_engine

from rxconcile.store.allowance import (
    DEFAULT_ALLOWANCE,
    view_for,
    year_label,
    year_window,
)
from rxconcile.store.models import EmployeeAllowance, ScanRecord

TODAY = dt.date(2026, 8, 31)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as opened:
        yield opened


def scan(
    session: Session,
    *,
    number: str = "EMP-4417",
    claimed: str = "0",
    year: str = "2026-27",
    scan_id: int | None = None,
    review_status: str = "reviewed",
) -> ScanRecord:
    record = ScanRecord(
        id=scan_id,
        employee_name="Yash", first_name="Yash", employee_number=number,
        user_email="employee@gmail.com", role="employee",
        prescription_filename="rx.png", bill_filename="bill.png",
        verdict="match", result_json=json.dumps({"verdict": "match", "findings": []}),
        decisions_json="{}", claimed_amount=Decimal(claimed), allowance_year=year,
        # Reviewed by default: these tests are about the arithmetic of a
        # settled claim. `test_a_submitted_claim_does_not_touch_the_balance`
        # covers the other state.
        review_status=review_status,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


# ---------------------------------------------------------------------------
# The year is stated, not implied
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "label"),
    [
        (dt.date(2026, 3, 31), "2025-26"),
        (dt.date(2026, 4, 1), "2026-27"),
        (dt.date(2026, 8, 31), "2026-27"),
        (dt.date(2027, 1, 5), "2026-27"),
    ],
)
def test_the_allowance_year_runs_april_to_march(when: dt.date, label: str) -> None:
    assert year_label(when) == label


def test_the_window_is_reported_so_used_so_far_can_be_checked() -> None:
    starts, ends = year_window("2026-27")
    assert starts == dt.date(2026, 4, 1)
    assert ends == dt.date(2027, 3, 31)


# ---------------------------------------------------------------------------
# Allowance and balance
# ---------------------------------------------------------------------------


def test_an_unknown_employee_gets_the_default_allowance(session: Session) -> None:
    view = view_for(session, "EMP-9999", today=TODAY)
    assert view.annual_amount == DEFAULT_ALLOWANCE == Decimal("12000.00")
    assert view.used == Decimal("0")
    assert view.balance == Decimal("12000.00")
    assert view.scans_counted == 0


def test_an_allowance_can_be_set_per_employee(session: Session) -> None:
    session.add(EmployeeAllowance(employee_number="EMP-4417", annual_amount=Decimal("25000")))
    session.commit()
    assert view_for(session, "EMP-4417", today=TODAY).annual_amount == Decimal("25000")


def test_used_so_far_sums_this_employees_claims_in_this_year(session: Session) -> None:
    scan(session, claimed="1200.00")
    scan(session, claimed="800.50")
    view = view_for(session, "EMP-4417", today=TODAY)
    assert view.used == Decimal("2000.50")
    assert view.scans_counted == 2
    assert view.balance == Decimal("9999.50")


def test_another_employees_claims_do_not_count(session: Session) -> None:
    scan(session, number="EMP-4417", claimed="1000")
    scan(session, number="ADM-0001", claimed="5000")
    assert view_for(session, "EMP-4417", today=TODAY).used == Decimal("1000")


def test_a_previous_years_claims_do_not_count(session: Session) -> None:
    scan(session, claimed="1000", year="2026-27")
    scan(session, claimed="9000", year="2025-26")
    assert view_for(session, "EMP-4417", today=TODAY).used == Decimal("1000")


def test_an_overdrawn_allowance_reports_nothing_left_not_a_negative(session: Session) -> None:
    scan(session, claimed="15000")
    view = view_for(session, "EMP-4417", today=TODAY)
    assert view.used == Decimal("15000")
    assert view.balance == Decimal("0"), "an employee does not owe the balance back"


def test_the_scan_being_viewed_can_be_left_out_of_used_so_far(session: Session) -> None:
    """So a result screen shows used-so-far BEFORE this claim, beside it."""
    earlier = scan(session, claimed="1000")
    current = scan(session, claimed="500")
    both = view_for(session, "EMP-4417", today=TODAY)
    assert both.used == Decimal("1500")
    before = view_for(session, "EMP-4417", today=TODAY, exclude_scan_id=current.id)
    assert before.used == Decimal("1000")
    assert before.scans_counted == 1
    assert earlier.id != current.id


# ---------------------------------------------------------------------------
# THE ONE TO GET RIGHT
# ---------------------------------------------------------------------------


def test_rejecting_a_line_reduces_the_claim_but_not_used_so_far(session: Session) -> None:
    """A rejection reduces THIS scan's claim. It never reduces the balance.

    Only accepted, claimable lines consume allowance. A scan whose lines were
    rejected simply claims less; it does not hand allowance back, and it does
    not reach into another scan's figures.
    """
    session.add(EmployeeAllowance(employee_number="EMP-4417", annual_amount=Decimal("12000")))
    session.commit()

    approved = scan(session, claimed="3000")
    before = view_for(session, "EMP-4417", today=TODAY)
    assert before.used == Decimal("3000")
    assert before.balance == Decimal("9000")

    # A second scan where the reviewer rejected everything: claims nothing.
    rejected = scan(session, claimed="0")
    after = view_for(session, "EMP-4417", today=TODAY)

    assert after.used == Decimal("3000"), "a rejected claim adds nothing"
    assert after.balance == Decimal("9000"), "and takes nothing away"
    assert after.used >= before.used, "used-so-far never goes DOWN because of a rejection"
    assert after.scans_counted == 2, "the scan still exists; it just claims nothing"
    assert rejected.claimed_amount == Decimal("0")
    assert approved.claimed_amount == Decimal("3000")


def test_revising_a_claim_downwards_only_moves_that_scans_amount(session: Session) -> None:
    first = scan(session, claimed="2000")
    scan(session, claimed="1000")
    assert view_for(session, "EMP-4417", today=TODAY).used == Decimal("3000")

    # The reviewer reopens the first scan and rejects half of it.
    first.claimed_amount = Decimal("500")
    session.add(first)
    session.commit()

    assert view_for(session, "EMP-4417", today=TODAY).used == Decimal("1500")
    assert view_for(session, "EMP-4417", today=TODAY).balance == Decimal("10500")


def test_a_submitted_claim_does_not_touch_the_balance(session: Session) -> None:
    """One definition of `used`, and it is reviewed-only.

    A submitted claim's amount comes from default decisions that no human has
    agreed to. Counting it would show a balance that moves the moment a
    reviewer rejects a line — worse than showing none.
    """
    scan(session, claimed="4000", review_status="reviewed")
    scan(session, claimed="2500", review_status="submitted")
    scan(session, claimed="1500", review_status="under_review")

    view = view_for(session, "EMP-4417", today=TODAY)
    assert view.used == Decimal("4000"), "only the reviewed claim is spent"
    assert view.balance == Decimal("8000")
    assert view.scans_counted == 1


def test_pending_work_is_reported_as_a_count_never_an_amount(session: Session) -> None:
    scan(session, claimed="4000", review_status="reviewed")
    scan(session, claimed="2500", review_status="submitted")
    scan(session, claimed="1500", review_status="under_review")

    view = view_for(session, "EMP-4417", today=TODAY)
    assert view.awaiting_review == 2
    # The pending amounts appear nowhere in the view, under any name.
    figures = {view.used, view.balance, view.annual_amount}
    assert Decimal("2500") not in figures
    assert Decimal("1500") not in figures
    assert Decimal("4000.00") not in {view.balance}


def test_reviewing_a_claim_is_what_spends_it(session: Session) -> None:
    """The transition, end to end."""
    record = scan(session, claimed="3000", review_status="submitted")
    assert view_for(session, "EMP-4417", today=TODAY).used == Decimal("0.00")

    record.review_status = "reviewed"
    session.add(record)
    session.commit()

    after = view_for(session, "EMP-4417", today=TODAY)
    assert after.used == Decimal("3000")
    assert after.awaiting_review == 0
