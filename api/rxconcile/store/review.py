"""Moving a submission through review.

One implementation, used by the endpoints AND by the demo seed. A seed that
wrote review state straight into the database could encode a shape the real
flow never produces — a reviewed scan with no reviewer, or an amount that no
decision adds up to — and the demo would then work while the feature did not.

The states are `submitted`, `under_review`, `reviewed`, and the transitions are
deliberately one-way: opening a claim starts a review, completing one ends it.
Nothing here sends a reviewed claim back, because nothing in the product asks
to yet and a transition with no caller is a guess about the future.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any, Final

from sqlmodel import Session

from rxconcile.store.allowance import year_label
from rxconcile.store.models import ScanRecord

SUBMITTED: Final[str] = "submitted"
UNDER_REVIEW: Final[str] = "under_review"
REVIEWED: Final[str] = "reviewed"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def open_review(session: Session, record: ScanRecord) -> ScanRecord:
    """Mark a submission as being looked at.

    Only from `submitted`. A finished review is not reopened by somebody
    reading it again — that would silently return an employee's allowance to
    them, because used-so-far counts reviewed claims only.
    """
    if record.review_status == SUBMITTED:
        record.review_status = UNDER_REVIEW
        session.add(record)
        session.commit()
        session.refresh(record)
    return record


def record_decisions(
    session: Session,
    record: ScanRecord,
    *,
    decisions: dict[str, Any],
    claimed_amount: Decimal,
) -> ScanRecord:
    """Store what the reviewer decided, line by line, and what it comes to.

    The amount arrives from the client because it is derived from the same rows
    the tables render. Recomputing it here from a second implementation is
    exactly how it would drift from the figure that was on screen when it was
    approved.
    """
    record.decisions_json = json.dumps(decisions)
    record.claimed_amount = claimed_amount
    # Stamped from the scan's OWN date, not today's. A record written before
    # allowance years existed carries a blank one, and an amount in a blank
    # year counts against nothing -- the claim would be recorded and then left
    # out of every balance on the system.
    if not record.allowance_year:
        record.allowance_year = year_label(record.created_at.date())
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def complete_review(session: Session, record: ScanRecord, *, reviewer: str) -> ScanRecord:
    """Finish a review, and only then let the claim touch the allowance.

    This is the moment `used` moves. Everything before it is provisional: the
    decisions on a submitted claim are defaults nobody has agreed to, which is
    why `usage()` counts reviewed scans only and why pending work is reported
    as a count and never as an amount.
    """
    record.review_status = REVIEWED
    record.reviewed_by = reviewer
    record.reviewed_at = _now()
    if not record.allowance_year:
        record.allowance_year = year_label(record.created_at.date())
    session.add(record)
    session.commit()
    session.refresh(record)
    return record
