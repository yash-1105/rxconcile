"""The additive migration and its backfill.

`ALTER TABLE ADD COLUMN` takes a constant default and nothing more, so anything
that depends on the row has to be written afterwards. These run against a real
SQLite file rather than the in-memory engine the rest of the suite uses,
because the migration is exactly what an in-memory database never exercises.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlmodel import Session, create_engine, select

from rxconcile.store.db import backfill, split_name
from rxconcile.store.models import ScanRecord


class TestSplitName:
    """One token, two, three — then stop guessing."""

    @pytest.mark.parametrize(
        ("full", "expected"),
        [
            ("", ("", "", "")),
            ("Yash", ("Yash", "", "")),
            ("Priya Nair", ("Priya", "", "Nair")),
            ("Anita Rao Desai", ("Anita", "Rao", "Desai")),
        ],
    )
    def test_the_shapes_it_can_read(self, full: str, expected: tuple[str, str, str]) -> None:
        assert split_name(full) == expected

    def test_four_or_more_words_goes_whole_into_first_name(self) -> None:
        """A double-barrelled surname, a patronymic, an honorific — there is no
        way to tell, so nothing is carved off. The same rule the engine
        follows: no value beats a wrong one."""
        assert split_name("Maria del Carmen Sanchez") == ("Maria del Carmen Sanchez", "", "")
        assert split_name("Dr A B C D") == ("Dr A B C D", "", "")

    def test_surrounding_whitespace_is_not_a_word(self) -> None:
        assert split_name("  Priya   Nair  ") == ("Priya", "", "Nair")


@pytest.fixture
def on_disk(tmp_path: Path) -> Engine:
    """A real file, because the migration only matters to one."""
    engine = create_engine(f"sqlite:///{tmp_path / 'scans.db'}")
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(engine)
    return engine


def _row(session: Session, **overrides: object) -> ScanRecord:
    record = ScanRecord(
        employee_name="Priya Nair", employee_number="EMP-1",
        user_email="employee@gmail.com", role="employee",
        prescription_filename="rx.png", bill_filename="bill.png",
        verdict="match", result_json=json.dumps({"verdict": "match"}),
        **overrides,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


class TestBackfill:
    def test_it_splits_a_name_that_has_no_parts_yet(self, on_disk: Engine) -> None:
        with Session(on_disk) as session:
            scan_id = _row(session, first_name="", middle_name="", last_name="").id
        backfill(on_disk)
        with Session(on_disk) as session:
            record = session.get(ScanRecord, scan_id)
            assert record is not None
            assert (record.first_name, record.middle_name, record.last_name) == (
                "Priya", "", "Nair",
            )

    def test_it_never_overwrites_parts_somebody_already_set(
        self, on_disk: Engine
    ) -> None:
        """A human's own answer beats a computed one."""
        with Session(on_disk) as session:
            scan_id = _row(session, first_name="Priyanka", last_name="Nair-Desai").id
        backfill(on_disk)
        with Session(on_disk) as session:
            record = session.get(ScanRecord, scan_id)
            assert record is not None
            assert record.first_name == "Priyanka"
            assert record.last_name == "Nair-Desai"

    def test_a_scan_with_decisions_backfills_as_reviewed(self, on_disk: Engine) -> None:
        with Session(on_disk) as session:
            scan_id = _row(
                session, review_status="",
                decisions_json=json.dumps({"rx-01-bill-01": {"decision": "accept"}}),
            ).id
        backfill(on_disk)
        with Session(on_disk) as session:
            record = session.get(ScanRecord, scan_id)
            assert record is not None
            assert record.review_status == "reviewed"

    def test_a_scan_nobody_touched_backfills_as_submitted(self, on_disk: Engine) -> None:
        with Session(on_disk) as session:
            scan_id = _row(session, review_status="", decisions_json="{}").id
        backfill(on_disk)
        with Session(on_disk) as session:
            record = session.get(ScanRecord, scan_id)
            assert record is not None
            assert record.review_status == "submitted"

    def test_running_it_twice_changes_nothing(self, on_disk: Engine) -> None:
        """It runs on every start, so it has to be safe to."""
        with Session(on_disk) as session:
            _row(session, first_name="", review_status="", decisions_json="{}")
        backfill(on_disk)
        with Session(on_disk) as session:
            before = [
                (r.first_name, r.last_name, r.review_status)
                for r in session.exec(select(ScanRecord)).all()
            ]
        backfill(on_disk)
        with Session(on_disk) as session:
            after = [
                (r.first_name, r.last_name, r.review_status)
                for r in session.exec(select(ScanRecord)).all()
            ]
        assert before == after


def test_every_new_column_lands_on_a_table_that_predates_it(tmp_path: Path) -> None:
    """The old-database case: create the table without the new columns, then
    migrate it the way a real start does."""
    from rxconcile.store.db import _add_missing_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE scan_record ("
            "id INTEGER PRIMARY KEY, created_at DATETIME, employee_name VARCHAR, "
            "employee_number VARCHAR, user_email VARCHAR, role VARCHAR, "
            "prescription_filename VARCHAR, bill_filename VARCHAR, verdict VARCHAR, "
            "discrepancy_count INTEGER, critical_count INTEGER, warning_count INTEGER, "
            "checks_unavailable_count INTEGER, result_json VARCHAR, "
            "processing_ms INTEGER, extraction_runs INTEGER)"
        ))
    _add_missing_columns(engine)
    with engine.begin() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(scan_record)"))}
    for name in (
        "first_name", "middle_name", "last_name",
        "certified_by_employee", "certified_at", "review_status",
        "decisions_json", "claimed_amount", "allowance_year",
    ):
        assert name in columns, f"{name} did not reach an existing database"
