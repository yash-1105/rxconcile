"""The review flow, and who is allowed to drive it.

Two things are load-bearing. Only a completed review may move an allowance —
everything before it is provisional, so a submission sitting in the queue costs
an employee nothing. And every reviewer route is closed to an employee with a
404, not a 403: the same answer a missing id gets, so probing teaches nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from rxconcile import main
from rxconcile.demo_auth import issue_token
from rxconcile.store import ScanRecord, set_engine
from rxconcile.store.allowance import view_for
from rxconcile.store.db import engine as get_engine

EMPLOYEE = "employee@gmail.com"
ADMIN = "admin@gmail.com"


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path) -> Iterator[None]:
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    SQLModel.metadata.create_all(engine)
    set_engine(engine)
    yield
    set_engine(None)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(main.app) as test_client:
        yield test_client


def auth(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(email)}"}


def submit(client: TestClient, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "first_name": "Yash", "employee_number": "EMP-4417",
        "prescription_filename": "rx.png", "bill_filename": "bill.png",
        "extraction_runs": 3,
        "result": {
            "verdict": "mismatch", "score": 60.0, "findings": [],
            "matched_pairs": [], "unmatched_prescribed": [], "unmatched_billed": [],
            "prescription": {"items": [], "overall_legibility": 0.9},
            "bill": {"items": [], "currency": "INR"}, "processing_ms": 100,
        },
    }
    body.update(overrides)
    response = client.post(
        "/api/scans", data={"payload": json.dumps(body)}, headers=auth(EMPLOYEE)
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


class TestOnlyAReviewerDrivesReview:
    """404 everywhere, never 403. An employee cannot tell what exists."""

    @pytest.mark.parametrize(
        "route", ["open-review", "complete-review"],
    )
    def test_an_employee_cannot_move_a_submission(
        self, client: TestClient, route: str
    ) -> None:
        scan = submit(client)
        refused = client.post(f"/api/scans/{scan['id']}/{route}", headers=auth(EMPLOYEE))
        assert refused.status_code == 404
        assert refused.json()["error_code"] == "SCAN_NOT_FOUND"

    def test_an_employee_cannot_record_decisions_on_their_own_claim(
        self, client: TestClient
    ) -> None:
        """They submit; they do not rule on it."""
        scan = submit(client)
        refused = client.patch(
            f"/api/scans/{scan['id']}/decisions",
            json={"decisions": {"a": {"decision": "accept"}}, "claimed_amount": "500"},
            headers=auth(EMPLOYEE),
        )
        assert refused.status_code == 404

    @pytest.mark.parametrize("fmt", ["pdf", "xlsx", "json"])
    def test_an_employee_cannot_export_their_own_scan(
        self, client: TestClient, fmt: str
    ) -> None:
        """An export is the whole analysis as a file.

        Leaving it on the owner rule handed an employee, in a download, exactly
        the comparison the JSON responses are careful never to send.
        """
        scan = submit(client)
        refused = client.get(
            f"/api/scans/{scan['id']}/export.{fmt}", headers=auth(EMPLOYEE)
        )
        assert refused.status_code == 404

    def test_a_reviewer_can_do_all_of_it(self, client: TestClient) -> None:
        scan = submit(client)
        assert client.post(
            f"/api/scans/{scan['id']}/open-review", headers=auth(ADMIN)
        ).status_code == 200
        assert client.get(
            f"/api/scans/{scan['id']}/export.json", headers=auth(ADMIN)
        ).status_code == 200


class TestTheStates:
    def test_a_new_submission_is_submitted_and_unreviewed(self, client: TestClient) -> None:
        scan = submit(client)
        assert scan["review_status"] == "submitted"
        assert scan["reviewed_by"] == ""
        assert scan["reviewed_at"] is None

    def test_opening_it_marks_it_under_review(self, client: TestClient) -> None:
        scan = submit(client)
        opened = client.post(
            f"/api/scans/{scan['id']}/open-review", headers=auth(ADMIN)
        ).json()
        assert opened["review_status"] == "under_review"

    def test_completing_it_stamps_who_and_when(self, client: TestClient) -> None:
        scan = submit(client)
        client.post(f"/api/scans/{scan['id']}/open-review", headers=auth(ADMIN))
        done = client.post(
            f"/api/scans/{scan['id']}/complete-review", headers=auth(ADMIN)
        ).json()
        assert done["review_status"] == "reviewed"
        assert done["reviewed_by"] == ADMIN
        assert done["reviewed_at"] is not None

    def test_reading_a_finished_review_does_not_reopen_it(self, client: TestClient) -> None:
        """Reopening would hand the employee their allowance back in silence,
        because used-so-far counts reviewed claims only."""
        scan = submit(client)
        client.post(f"/api/scans/{scan['id']}/complete-review", headers=auth(ADMIN))
        again = client.post(
            f"/api/scans/{scan['id']}/open-review", headers=auth(ADMIN)
        ).json()
        assert again["review_status"] == "reviewed"


class TestOnlyACompletedReviewSpends:
    def test_a_queued_submission_moves_no_balance(self, client: TestClient) -> None:
        scan = submit(client)
        client.patch(
            f"/api/scans/{scan['id']}/decisions",
            json={"decisions": {}, "claimed_amount": "2500.00"},
            headers=auth(ADMIN),
        )
        with Session(get_engine()) as session:
            view = view_for(session, "EMP-4417")
        assert view.used == Decimal("0.00"), "a claim nobody finished is not spent"
        assert view.awaiting_review == 1

    def test_completing_the_review_is_what_spends_it(self, client: TestClient) -> None:
        scan = submit(client)
        client.patch(
            f"/api/scans/{scan['id']}/decisions",
            json={"decisions": {}, "claimed_amount": "2500.00"},
            headers=auth(ADMIN),
        )
        client.post(f"/api/scans/{scan['id']}/complete-review", headers=auth(ADMIN))
        with Session(get_engine()) as session:
            view = view_for(session, "EMP-4417")
        assert view.used == Decimal("2500.00")
        assert view.balance == Decimal("9500.00")
        assert view.awaiting_review == 0

    def test_a_rejected_line_never_reaches_the_allowance(self, client: TestClient) -> None:
        """The invariant, end to end through the real routes.

        The reviewer accepts one line and rejects another; only the accepted
        amount is sent, and only that is spent.
        """
        scan = submit(client)
        client.patch(
            f"/api/scans/{scan['id']}/decisions",
            json={
                "decisions": {
                    "rx-01-bill-01": {"decision": "accept"},
                    "rx-02-bill-02": {"decision": "reject", "remark": "Not prescribed"},
                },
                # The accepted line only. The rejected 400 is not in it.
                "claimed_amount": "600.00",
            },
            headers=auth(ADMIN),
        )
        client.post(f"/api/scans/{scan['id']}/complete-review", headers=auth(ADMIN))
        with Session(get_engine()) as session:
            view = view_for(session, "EMP-4417")
        assert view.used == Decimal("600.00")
        assert view.balance == Decimal("11400.00")

    def test_one_employees_review_does_not_touch_another(self, client: TestClient) -> None:
        mine = submit(client)
        theirs = submit(client, employee_number="EMP-9001", first_name="Asha")
        for scan_id in (mine["id"], theirs["id"]):
            client.patch(
                f"/api/scans/{scan_id}/decisions",
                json={"decisions": {}, "claimed_amount": "1000.00"},
                headers=auth(ADMIN),
            )
        client.post(f"/api/scans/{mine['id']}/complete-review", headers=auth(ADMIN))
        with Session(get_engine()) as session:
            assert view_for(session, "EMP-4417").used == Decimal("1000.00")
            assert view_for(session, "EMP-9001").used == Decimal("0.00")


def test_an_uncertified_submission_is_still_visible_to_the_reviewer(
    client: TestClient,
) -> None:
    """It has not really been submitted, so the queue has to be able to say so
    rather than hide it."""
    scan = submit(client)
    rows = client.get("/api/scans", headers=auth(ADMIN)).json()
    row = next(r for r in rows if r["id"] == scan["id"])
    assert row["certified_by_employee"] is False
    assert row["certified_at"] is None


def test_the_reviewer_is_the_account_not_a_typed_name(client: TestClient) -> None:
    """Identity comes from the token. Nothing in the request can set it."""
    scan = submit(client)
    done = client.post(
        f"/api/scans/{scan['id']}/complete-review",
        json={"reviewed_by": "somebody-else@example.com"},
        headers=auth(ADMIN),
    ).json()
    assert done["reviewed_by"] == ADMIN


def test_a_scan_row_survives_the_new_columns(client: TestClient) -> None:
    scan = submit(client)
    with Session(get_engine()) as session:
        record = session.get(ScanRecord, scan["id"])
        assert record is not None
        assert record.reviewed_by == ""
        assert record.reviewed_at is None
