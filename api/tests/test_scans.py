"""Scan persistence and role-derived filtering.

The point of these tests is the one property the demo login actually has: the
server decides who the caller is from a token it issued, so a caller cannot
widen what they see by asserting a role.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from rxconcile import main
from rxconcile.demo_auth import issue_token
from rxconcile.store import set_engine


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


def result_payload(verdict: str = "mismatch") -> dict[str, Any]:
    return {
        "verdict": verdict,
        "score": 17.0,
        "processing_ms": 1234,
        "findings": [
            {"rule_code": "STRENGTH_MISMATCH", "severity": "critical", "message": "m"},
            {"rule_code": "FORM_MISMATCH", "severity": "warning", "message": "m"},
            {"rule_code": "CHECK_UNAVAILABLE", "severity": "info", "message": "m"},
            {"rule_code": "LOW_CONFIDENCE_FIELD", "severity": "info", "message": "m"},
        ],
        "prescription": {"items": []},
        "bill": {"items": []},
    }


def save(client: TestClient, email: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "employee_name": "Priya Nair",
        "employee_number": "EMP-4417",
        "prescription_filename": "rx.jpg",
        "bill_filename": "bill.png",
        "extraction_runs": 3,
        "result": result_payload(),
    }
    body.update(overrides)
    response = client.post("/api/scans", json=body, headers=auth(email))
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
        json={
            "employee_name": "Priya Nair",
            "employee_number": "EMP-4417",
            "extraction_runs": 3,
            "result": result_payload(),
            # Both ignored: not fields on the request model.
            "user_email": ADMIN,
            "role": "admin",
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
