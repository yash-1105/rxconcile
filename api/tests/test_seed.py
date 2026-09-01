"""The demo seed must produce what the real flow produces.

A seed is the only place in this project that writes scan records without
going through an upload, and that makes it the easiest place for a shape to
drift. It already happened once: every seeded payload carried a `duration_days`
key on billed lines, which `BilledItem` forbids, so all six seeded rows were
stored unreadable and would have failed the moment a reviewer opened one. The
seed ran clean the whole time, because nothing parsed what it wrote.

So these tests assert the two things a seed can silently get wrong: that what
it stores can be read back, and that its reviewed rows went through the review
service rather than being typed into the row.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, col, create_engine, select

from rxconcile.export.rows import claimable_amount, medicine_rows
from rxconcile.export.rows import test_rows as lab_rows_of
from rxconcile.models import ReconciliationResult
from rxconcile.store import ScanRecord
from rxconcile.store.review import REVIEWED, SUBMITTED
from scripts.seed_demo import seed


@pytest.fixture()
def seeded() -> list[ScanRecord]:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session, today=dt.date(2026, 3, 1))
        return list(session.exec(select(ScanRecord).order_by(col(ScanRecord.id))).all())


def test_every_seeded_payload_reads_back(seeded: list[ScanRecord]) -> None:
    """What the seed stores, the app can open.

    This is the regression that shipped: a payload the seed wrote happily and
    the result screen could not parse.
    """
    for record in seeded:
        ReconciliationResult.model_validate(json.loads(record.result_json))


def test_reviewed_rows_carry_a_reviewer(seeded: list[ScanRecord]) -> None:
    """A reviewed claim has someone's name and a time on it.

    Only `complete_review` sets these three together. A row that said
    `reviewed` without them would be a state the real flow cannot produce.
    """
    reviewed = [r for r in seeded if r.review_status == REVIEWED]
    assert reviewed, "the demo needs reviewed claims to show a finished review"
    for record in reviewed:
        assert record.reviewed_by
        assert record.reviewed_at is not None
        assert json.loads(record.decisions_json), "reviewed with no decisions"


def test_enough_left_pending_to_demonstrate_the_queue(seeded: list[ScanRecord]) -> None:
    pending = [r for r in seeded if r.review_status == SUBMITTED]
    assert len(pending) >= 3
    for record in pending:
        # Nothing about a queued claim may look reviewed.
        assert not record.reviewed_by
        assert record.reviewed_at is None
        assert record.claimed_amount is None or record.claimed_amount == 0


def test_a_rejected_line_is_not_claimed(seeded: list[ScanRecord]) -> None:
    """The point of seeding a rejection: it must cost the employee nothing.

    The seeded rejection sits on the multi-line submission, so the claim is
    reduced rather than zeroed -- a claim worth nothing would not show that a
    rejection is subtracted rather than fatal.
    """
    def has_rejection(record: ScanRecord) -> bool:
        stored: dict[str, Any] = json.loads(record.decisions_json)
        return any(
            isinstance(d, dict) and d.get("decision") == "reject" for d in stored.values()
        )

    rejected = [r for r in seeded if has_rejection(r)]
    assert rejected, "the demo needs a rejected line"
    for record in rejected:
        result = ReconciliationResult.model_validate(json.loads(record.result_json))
        full = sum(
            [
                *(claimable_amount(row) for row in medicine_rows(result)),
                *(claimable_amount(row) for row in lab_rows_of(result)),
            ],
            Decimal("0"),
        )
        assert record.claimed_amount is not None
        assert 0 < record.claimed_amount < full
