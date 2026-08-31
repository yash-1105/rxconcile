"""Scan persistence and role-derived filtering.

The point of these tests is the one property the demo login actually has: the
server decides who the caller is from a token it issued, so a caller cannot
widen what they see by asserting a role.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from rxconcile import main
from rxconcile.demo_auth import issue_token
from rxconcile.store import ScanRecord, set_engine
from rxconcile.store.db import engine as get_engine


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path) -> Iterator[None]:
    """Each test gets its own database file; nothing touches api/data."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
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


EMPLOYEE = "employee@gmail.com"
ADMIN = "admin@gmail.com"


def result_payload() -> dict[str, Any]:
    """A complete, valid ReconciliationResult, as the API actually stores one."""
    return {
        "verdict": "mismatch",
        "score": 60.0,
        "findings": [
            {"rule_code": "STRENGTH_MISMATCH", "severity": "critical", "message": "m",
             "prescribed_ref": None, "billed_ref": None, "detail": {}},
            {"rule_code": "FORM_MISMATCH", "severity": "warning", "message": "m",
             "prescribed_ref": None, "billed_ref": None, "detail": {}},
            {"rule_code": "CHECK_UNAVAILABLE", "severity": "info", "message": "m",
             "prescribed_ref": None, "billed_ref": None, "detail": {}},
            {"rule_code": "LOW_CONFIDENCE_FIELD", "severity": "info", "message": "m",
             "prescribed_ref": None, "billed_ref": None, "detail": {}},
        ],
        "matched_pairs": [],
        "unmatched_prescribed": [],
        "unmatched_billed": [],
        "prescription": {"items": [], "overall_legibility": 0.9},
        "bill": {"items": [], "currency": "INR"},
        "processing_ms": 120,
    }


def save(
    client: TestClient,
    email: str,
    *,
    files: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "employee_name": "Yash",
        "employee_number": "EMP-4417",
        "prescription_filename": "rx.jpg",
        "bill_filename": "bill.png",
        "extraction_runs": 3,
        "result": result_payload(),
    }
    body.update(overrides)
    # Multipart, because the source pages travel with the save. They are
    # optional: a scan must still record when no image is supplied.
    response = client.post(
        "/api/scans",
        data={"payload": json.dumps(body)},
        files=files or {},
        headers=auth(email),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


# --------------------------------------------------------------------------
# Demo session
# --------------------------------------------------------------------------


def test_demo_session_returns_a_token(client: TestClient) -> None:
    response = client.post(
        "/api/demo/session", json={"email": ADMIN, "password": "admin123"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "admin"
    assert body["token"].startswith(ADMIN)


def test_bad_credentials_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/demo/session", json={"email": ADMIN, "password": "nope"}
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "BAD_DEMO_CREDENTIALS"


def test_endpoints_require_a_session(client: TestClient) -> None:
    assert client.get("/api/scans").status_code == 401
    assert client.get("/api/scans", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_a_forged_token_is_rejected(client: TestClient) -> None:
    """Swapping the email in a valid token must not work."""
    valid = issue_token(EMPLOYEE)
    forged = valid.replace(EMPLOYEE, ADMIN)
    response = client.get("/api/scans", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------


def test_summary_columns_are_derived_from_the_result(client: TestClient) -> None:
    saved = save(client, EMPLOYEE)
    assert saved["verdict"] == "mismatch"
    assert saved["critical_count"] == 1
    assert saved["warning_count"] == 1
    assert saved["discrepancy_count"] == 2
    # A check that could not run is counted separately, never as a discrepancy.
    assert saved["checks_unavailable_count"] == 1


def test_the_full_result_is_stored_verbatim(client: TestClient) -> None:
    saved = save(client, EMPLOYEE)
    detail = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).json()
    assert detail["result"] == result_payload()


def test_identity_comes_from_the_token_not_the_body(client: TestClient) -> None:
    """A caller cannot file a scan under someone else or claim a role."""
    response = client.post(
        "/api/scans",
        data={
            "payload": json.dumps({
                "employee_name": "Yash",
                "employee_number": "EMP-4417",
                "extraction_runs": 3,
                "result": result_payload(),
                # Both ignored: not fields on the request model.
                "user_email": ADMIN,
                "role": "admin",
            })
        },
        headers=auth(EMPLOYEE),
    )
    assert response.status_code == 200
    assert response.json()["user_email"] == EMPLOYEE
    assert response.json()["role"] == "employee"


# --------------------------------------------------------------------------
# Role-derived filtering — the reason this is server-side
# --------------------------------------------------------------------------


def test_employee_sees_only_their_own_scans(client: TestClient) -> None:
    save(client, EMPLOYEE)
    save(client, ADMIN)
    rows = client.get("/api/scans", headers=auth(EMPLOYEE)).json()
    assert len(rows) == 1
    assert all(row["user_email"] == EMPLOYEE for row in rows)


def test_admin_sees_every_scan(client: TestClient) -> None:
    save(client, EMPLOYEE)
    save(client, ADMIN)
    rows = client.get("/api/scans", headers=auth(ADMIN)).json()
    assert len(rows) == 2
    assert {row["user_email"] for row in rows} == {EMPLOYEE, ADMIN}


def test_claiming_admin_in_the_request_changes_nothing(client: TestClient) -> None:
    """The whole point: role is never read from anything the caller controls."""
    save(client, EMPLOYEE)
    save(client, ADMIN)
    for attempt in (
        {"Authorization": f"Bearer {issue_token(EMPLOYEE)}", "X-Role": "admin"},
        {"Authorization": f"Bearer {issue_token(EMPLOYEE)}", "role": "admin"},
    ):
        rows = client.get("/api/scans", headers=attempt).json()
        assert len(rows) == 1, "a header claiming admin widened the result"


def test_employee_cannot_open_another_users_scan(client: TestClient) -> None:
    other = save(client, ADMIN)
    response = client.get(f"/api/scans/{other['id']}", headers=auth(EMPLOYEE))
    # 404 rather than 403: probing ids reveals nothing about what exists.
    assert response.status_code == 404


def test_admin_can_open_an_employees_scan(client: TestClient) -> None:
    theirs = save(client, EMPLOYEE)
    response = client.get(f"/api/scans/{theirs['id']}", headers=auth(ADMIN))
    assert response.status_code == 200


# --------------------------------------------------------------------------
# Deletion
# --------------------------------------------------------------------------


def test_admin_can_delete(client: TestClient) -> None:
    saved = save(client, EMPLOYEE)
    assert client.delete(f"/api/scans/{saved['id']}", headers=auth(ADMIN)).status_code == 200
    assert client.get("/api/scans", headers=auth(ADMIN)).json() == []


def test_employee_cannot_delete(client: TestClient) -> None:
    saved = save(client, EMPLOYEE)
    response = client.delete(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE))
    assert response.status_code == 403
    assert response.json()["error_code"] == "NOT_PERMITTED_IN_DEMO"
    assert len(client.get("/api/scans", headers=auth(EMPLOYEE)).json()) == 1


def test_deleting_a_missing_scan_is_reported(client: TestClient) -> None:
    response = client.delete("/api/scans/9999", headers=auth(ADMIN))
    assert response.status_code == 404


def test_listing_is_newest_first(client: TestClient) -> None:
    first = save(client, EMPLOYEE)
    second = save(client, EMPLOYEE)
    rows = client.get("/api/scans", headers=auth(EMPLOYEE)).json()
    assert [row["id"] for row in rows] == [second["id"], first["id"]]


def test_result_json_survives_a_schema_the_columns_do_not_know_about(
    client: TestClient,
) -> None:
    """The blob is why this schema can keep changing.

    A field nobody has a column for must still come back intact.
    """
    payload = result_payload()
    payload["some_future_field"] = {"lab_tests": [{"name": "CBC", "billed": True}]}
    saved = save(client, EMPLOYEE, result=payload)
    detail = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).json()
    assert detail["result"]["some_future_field"]["lab_tests"][0]["name"] == "CBC"
    assert json.loads(json.dumps(detail["result"])) == payload


# --------------------------------------------------------------------------
# Source pages and exports
# --------------------------------------------------------------------------


def _jpeg() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (300, 200), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_a_scan_saves_without_any_source_pages(client: TestClient) -> None:
    """A save must never fail for want of an image."""
    saved = save(client, EMPLOYEE)
    response = client.get(f"/api/scans/{saved['id']}/image/prescription", headers=auth(EMPLOYEE))
    assert response.status_code == 404
    assert response.json()["error_code"] == "IMAGE_NOT_STORED"


def test_stored_pages_come_back_and_are_preprocessed(client: TestClient) -> None:
    page = _jpeg()
    saved = save(
        client, EMPLOYEE,
        files={"prescription": ("rx.jpg", page, "image/jpeg"),
               "bill": ("bill.jpg", page, "image/jpeg")},
    )
    for which in ("prescription", "bill"):
        response = client.get(f"/api/scans/{saved['id']}/image/{which}", headers=auth(EMPLOYEE))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/")
        # Preprocessed, not the original bytes: this is what the model saw, and
        # what the bounding boxes are normalised against.
        assert response.content.startswith(b"\xff\xd8")


def test_an_employee_cannot_read_another_accounts_pages(client: TestClient) -> None:
    page = _jpeg()
    saved = save(client, ADMIN, files={"prescription": ("rx.jpg", page, "image/jpeg")})
    response = client.get(f"/api/scans/{saved['id']}/image/prescription", headers=auth(EMPLOYEE))
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("fmt", "prefix"),
    [("pdf", b"%PDF-"), ("xlsx", b"PK"), ("json", b"{")],
)
def test_every_export_format_downloads(client: TestClient, fmt: str, prefix: bytes) -> None:
    saved = save(client, EMPLOYEE)
    response = client.get(f"/api/scans/{saved['id']}/export.{fmt}", headers=auth(EMPLOYEE))
    assert response.status_code == 200, response.text
    assert response.content.startswith(prefix)
    assert "attachment" in response.headers["content-disposition"]


def test_an_unknown_export_format_is_rejected(client: TestClient) -> None:
    saved = save(client, EMPLOYEE)
    response = client.get(f"/api/scans/{saved['id']}/export.docx", headers=auth(EMPLOYEE))
    assert response.status_code == 404
    assert response.json()["error_code"] == "UNKNOWN_FORMAT"


def test_an_employee_cannot_export_another_accounts_scan(client: TestClient) -> None:
    saved = save(client, ADMIN)
    response = client.get(f"/api/scans/{saved['id']}/export.json", headers=auth(EMPLOYEE))
    assert response.status_code == 404


def test_an_export_of_a_legacy_record_still_builds(client: TestClient) -> None:
    """Records written before later schema additions must still export."""
    # The shape a record written before lab tests, canonical matches and the
    # reimbursement assessment existed: valid, just missing later additions.
    legacy = result_payload()
    for later_addition in ("canonical", "reimbursement", "matched_tests"):
        legacy.pop(later_addition, None)
    saved = save(client, EMPLOYEE, result=legacy)
    for fmt in ("pdf", "xlsx", "json"):
        response = client.get(f"/api/scans/{saved['id']}/export.{fmt}", headers=auth(EMPLOYEE))
        assert response.status_code == 200, f"{fmt}: {response.text}"


class TestDecisionsSurviveTheRecord:
    """Accept/reject is a record, so it has to come back the way it went in."""

    def test_decisions_are_returned_when_the_scan_is_reopened(
        self, client: TestClient
    ) -> None:
        scan = save(client, EMPLOYEE)
        decisions = {
            "rx-01-bill-01": {"decision": "reject", "remark": "80mg billed against 40mg"},
            "bill-only-bill-02": {"decision": "unset"},
        }
        revised = client.patch(
            f"/api/scans/{scan['id']}/decisions",
            json={"decisions": decisions, "claimed_amount": "410.50"},
            headers=auth(EMPLOYEE),
        )
        assert revised.status_code == 200

        reopened = client.get(f"/api/scans/{scan['id']}", headers=auth(EMPLOYEE)).json()
        assert reopened["decisions"] == decisions
        assert reopened["claimed_amount"] == "410.50"

    def test_a_scan_nobody_has_reviewed_returns_no_decisions(
        self, client: TestClient
    ) -> None:
        """Not an empty approval -- an absence, which the screen reads as undecided."""
        scan = save(client, EMPLOYEE)
        reopened = client.get(f"/api/scans/{scan['id']}", headers=auth(EMPLOYEE)).json()
        assert reopened["decisions"] == {}

    def test_deciding_on_an_older_scan_stamps_the_year_it_belongs_to(
        self, client: TestClient
    ) -> None:
        """The claim would otherwise count against nothing.

        A record written before allowance years existed carries a blank one, and
        `usage()` matches on the year. Without the backfill the amount is stored,
        shown on the scan, and left out of every balance on the system.
        """
        scan = save(client, EMPLOYEE)
        record_id = int(scan["id"])
        with Session(get_engine()) as session:
            stored = session.get(ScanRecord, record_id)
            assert stored is not None
            stored.allowance_year = ""  # as a pre-allowance record was written
            stored.created_at = dt.datetime(2025, 6, 1, 10, 0)
            session.add(stored)
            session.commit()

        client.patch(
            f"/api/scans/{record_id}/decisions",
            json={"decisions": {}, "claimed_amount": "900.00"},
            headers=auth(EMPLOYEE),
        )
        with Session(get_engine()) as session:
            stored = session.get(ScanRecord, record_id)
            assert stored is not None
            # 1 June 2025 falls in the Indian financial year 2025-26, NOT today's.
            assert stored.allowance_year == "2025-26"

    def test_the_year_already_on_a_scan_is_never_moved(self, client: TestClient) -> None:
        scan = save(client, EMPLOYEE)
        record_id = int(scan["id"])
        with Session(get_engine()) as session:
            stored = session.get(ScanRecord, record_id)
            assert stored is not None
            stored.allowance_year = "2024-25"
            session.add(stored)
            session.commit()

        client.patch(
            f"/api/scans/{record_id}/decisions",
            json={"decisions": {}, "claimed_amount": "50.00"},
            headers=auth(EMPLOYEE),
        )
        with Session(get_engine()) as session:
            stored = session.get(ScanRecord, record_id)
            assert stored is not None
            assert stored.allowance_year == "2024-25"


class TestAllowanceIsRoleFiltered:
    """An employee number is not a key to somebody else's spending.

    `/api/allowance/{number}` had no role check, so any signed-in account could
    read any colleague's annual allowance, used-so-far, balance and scan count
    by typing their number — the leak the scan endpoints are careful to avoid.
    """

    def test_an_employee_can_read_their_own(self, client: TestClient) -> None:
        save(client, EMPLOYEE)
        got = client.get("/api/allowance/EMP-4417", headers=auth(EMPLOYEE))
        assert got.status_code == 200
        assert got.json()["employee_number"] == "EMP-4417"

    def test_an_employee_cannot_read_another_number(self, client: TestClient) -> None:
        save(client, EMPLOYEE)
        save(client, ADMIN, employee_number="ADM-0001", employee_name="Ishan")
        denied = client.get("/api/allowance/ADM-0001", headers=auth(EMPLOYEE))
        assert denied.status_code == 404

    def test_probing_a_number_that_does_not_exist_looks_the_same(
        self, client: TestClient
    ) -> None:
        """Identical response either way, so the endpoint confirms nothing."""
        save(client, EMPLOYEE)
        real = client.get("/api/allowance/ADM-0001", headers=auth(EMPLOYEE))
        invented = client.get("/api/allowance/EMP-9999", headers=auth(EMPLOYEE))
        assert real.status_code == invented.status_code == 404
        assert real.json()["error_code"] == invented.json()["error_code"]

    def test_an_admin_reads_any_number(self, client: TestClient) -> None:
        save(client, EMPLOYEE)
        got = client.get("/api/allowance/EMP-4417", headers=auth(ADMIN))
        assert got.status_code == 200
