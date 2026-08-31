"""Annual reimbursement allowance, and what has been drawn against it.

Two properties this has to hold, because they are the first things anyone
checks by hand:

**Only accepted, claimable lines consume allowance.** A rejected line reduces
the claim for its own scan and nothing else. It never reduces -- or inflates --
the employee's used-so-far or balance.

**The window is stated, not implied.** "Used so far" means nothing without
saying since when, so the year is computed explicitly and travels with every
figure.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from rxconcile.store.models import EmployeeAllowance, ScanRecord

#: The allowance year runs April to March, the Indian financial year.
YEAR_START_MONTH: Final[int] = 4

DEFAULT_ALLOWANCE: Final[Decimal] = Decimal("12000.00")


def year_label(when: dt.date) -> str:
    """The allowance year a date falls in, e.g. ``2026-27``."""
    start = when.year if when.month >= YEAR_START_MONTH else when.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def year_window(label: str) -> tuple[dt.date, dt.date]:
    """First and last day of a labelled allowance year."""
    start_year = int(label.split("-")[0])
    return (
        dt.date(start_year, YEAR_START_MONTH, 1),
        dt.date(start_year + 1, YEAR_START_MONTH, 1) - dt.timedelta(days=1),
    )


class AllowanceView(BaseModel):
    """An employee's allowance and what is left of it.

    Every field a reader might otherwise have to take on trust is here: the
    window, how many scans were counted, and the amount.
    """

    model_config = ConfigDict(frozen=True)

    employee_number: str
    employee_name: str = ""
    year: str
    year_starts: dt.date
    year_ends: dt.date
    annual_amount: Decimal
    used: Decimal = Field(
        description="Sum of the claimed amounts on this employee's scans in this year. "
        "Only accepted, claimable lines are in those amounts."
    )
    scans_counted: int = 0
    balance: Decimal = Decimal("0")


def allowance_for(session: Session, employee_number: str) -> Decimal:
    row = session.get(EmployeeAllowance, employee_number)
    return row.annual_amount if row else DEFAULT_ALLOWANCE


def usage(
    session: Session,
    employee_number: str,
    *,
    year: str,
    exclude_scan_id: int | None = None,
) -> tuple[Decimal, int]:
    """What this employee has drawn in a year, and over how many scans.

    ``exclude_scan_id`` leaves the scan being viewed out, so a result screen can
    show "used so far" as it stood BEFORE this claim rather than double-counting
    the claim it is displaying beside it.
    """
    statement = select(ScanRecord).where(
        ScanRecord.employee_number == employee_number,
        ScanRecord.allowance_year == year,
    )
    records = [
        record
        for record in session.exec(statement).all()
        if exclude_scan_id is None or record.id != exclude_scan_id
    ]
    total = sum((record.claimed_amount for record in records), Decimal("0"))
    return total.quantize(Decimal("0.01")), len(records)


def view_for(
    session: Session,
    employee_number: str,
    *,
    employee_name: str = "",
    today: dt.date | None = None,
    exclude_scan_id: int | None = None,
) -> AllowanceView:
    label = year_label(today or dt.date.today())
    starts, ends = year_window(label)
    annual = allowance_for(session, employee_number)
    used, counted = usage(session, employee_number, year=label, exclude_scan_id=exclude_scan_id)
    stored = session.get(EmployeeAllowance, employee_number)
    return AllowanceView(
        employee_number=employee_number,
        employee_name=stored.employee_name if stored else employee_name,
        year=label,
        year_starts=starts,
        year_ends=ends,
        annual_amount=annual,
        used=used,
        scans_counted=counted,
        # Never below zero: an overdrawn allowance is reported as nothing left,
        # not as a negative balance the employee somehow owes.
        balance=max(Decimal("0"), annual - used),
    )
