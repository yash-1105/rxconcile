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
        "first_name": "Yash",
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
    """Read back as an ADMIN: an employee is not shown the result at all."""
    saved = save(client, EMPLOYEE)
    detail = client.get(f"/api/scans/{saved['id']}", headers=auth(ADMIN)).json()
    assert detail["result"] == result_payload()


def test_identity_comes_from_the_token_not_the_body(client: TestClient) -> None:
    """A caller cannot file a scan under someone else or claim a role."""
    response = client.post(
        "/api/scans",
        data={
            "payload": json.dumps({
                "first_name": "Yash",
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
    # POST /api/scans answers with the reviewer summary, which carries the
    # account the token was bound to.
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
    # `user_email` is not in the employee's shape; the narrowing is asserted
    # through the admin's view of the same two records.
    seen = client.get("/api/scans", headers=auth(ADMIN)).json()
    mine = [row for row in seen if row["user_email"] == EMPLOYEE]
    assert [row["id"] for row in rows] == [row["id"] for row in mine]


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
    detail = client.get(f"/api/scans/{saved['id']}", headers=auth(ADMIN)).json()
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
    response = client.get(f"/api/scans/{saved['id']}/export.{fmt}", headers=auth(ADMIN))
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
        response = client.get(f"/api/scans/{saved['id']}/export.{fmt}", headers=auth(ADMIN))
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
            headers=auth(ADMIN),
        )
        assert revised.status_code == 200

        reopened = client.get(f"/api/scans/{scan['id']}", headers=auth(ADMIN)).json()
        assert reopened["decisions"] == decisions
        assert reopened["claimed_amount"] == "410.50"

    def test_a_scan_nobody_has_reviewed_returns_no_decisions(
        self, client: TestClient
    ) -> None:
        """Not an empty approval -- an absence, which the screen reads as undecided."""
        scan = save(client, EMPLOYEE)
        reopened = client.get(f"/api/scans/{scan['id']}", headers=auth(ADMIN)).json()
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
            headers=auth(ADMIN),
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
            headers=auth(ADMIN),
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
        save(client, ADMIN, employee_number="ADM-0001", first_name="Ishan")
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


class TestTheEmployeeShapeIsAnAllowList:
    """What an employee's own responses are allowed to contain.

    Key-SET equality, not a list of absences. A field added to `ScanSummary`
    later cannot leak into the employee's view without failing here — and a
    leak would otherwise be silent, which is what makes it worth pinning.
    """

    LIST_KEYS = {
        "id", "created_at", "employee_name", "first_name", "middle_name", "last_name",
        "employee_number", "condition", "description",
        "prescription_filename", "bill_filename",
        "lab_report_filename", "lab_bill_filename",
        "review_status", "certified_by_employee", "certified_at",
    }
    DETAIL_KEYS = LIST_KEYS | {"readability", "content"}

    #: Everything the employee submits but does not review. Named individually
    #: so a failure says which figure came back.
    FORBIDDEN = (
        "result", "decisions", "verdict", "score",
        "discrepancy_count", "critical_count", "warning_count",
        "checks_unavailable_count", "eligible_total", "currency",
        "claimed_amount", "allowance_year", "processing_ms", "extraction_runs",
        "user_email", "role",
    )

    #: Keys that would mean the COMPARISON had reached the submitter. The
    #: transcription is what was read off each document; the relationship
    #: between the documents is never theirs to see.
    COMPARISON_KEYS = (
        "findings", "matched_pairs", "matched_tests", "unmatched_prescribed",
        "unmatched_billed", "unmatched_prescribed_tests", "unmatched_billed_tests",
        "reimbursement", "canonical", "review_summary", "rule_code", "severity",
        "eligible", "not_eligible", "non_medicine", "claimable", "supported",
    )

    def test_the_list_carries_exactly_these_keys(self, client: TestClient) -> None:
        save(client, EMPLOYEE)
        rows = client.get("/api/scans", headers=auth(EMPLOYEE)).json()
        assert rows, "fixture must produce a row"
        assert set(rows[0]) == self.LIST_KEYS

    def test_the_detail_carries_exactly_these_keys(self, client: TestClient) -> None:
        saved = save(client, EMPLOYEE)
        detail = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).json()
        assert set(detail) == self.DETAIL_KEYS

    def test_no_forbidden_key_appears_anywhere_in_either(
        self, client: TestClient
    ) -> None:
        saved = save(client, EMPLOYEE)
        rows = client.get("/api/scans", headers=auth(EMPLOYEE)).json()
        detail = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).json()
        for key in self.FORBIDDEN:
            assert key not in rows[0], f"{key!r} leaked into the employee's history"
            assert key not in detail, f"{key!r} leaked into the employee's submission"

    def test_no_forbidden_value_is_reachable_by_serialising_the_whole_body(
        self, client: TestClient
    ) -> None:
        """Not just absent at the top level: absent, full stop.

        A nested object carrying the verdict would satisfy a key check and
        still put the analysis on the wire.
        """
        saved = save(client, EMPLOYEE)
        detail = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).text
        for word in ("mismatch", "STRENGTH_MISMATCH", "FORM_MISMATCH", "discrepancy"):
            assert word not in detail

    def test_a_completed_review_does_not_widen_the_shape(
        self, client: TestClient
    ) -> None:
        """The review is where the forbidden figures come into existence.

        Completing one writes a claimed amount, a reviewer and a timestamp onto
        the record. None of that may reach the submitter: they are told their
        claim is reviewed, not what it was reduced to and by whom. Checked
        after a real review rather than on a fresh submission, because a
        submission has no amount to leak yet -- which is exactly how a widened
        shape would slip past the tests above.
        """
        saved = save(client, EMPLOYEE)
        assert client.post(
            f"/api/scans/{saved['id']}/open-review", headers=auth(ADMIN)
        ).status_code == 200
        assert client.patch(
            f"/api/scans/{saved['id']}/decisions",
            json={
                "decisions": {"rx-01-bill-01": {"decision": "accept"}},
                "claimed_amount": "700.00",
            },
            headers=auth(ADMIN),
        ).status_code == 200
        assert client.post(
            f"/api/scans/{saved['id']}/complete-review", headers=auth(ADMIN)
        ).status_code == 200

        # The reviewer's own view carries all three, which is what makes the
        # assertions below meaningful: there is something real to leak here,
        # so an empty response could not pass this test by accident.
        seen = client.get(f"/api/scans/{saved['id']}", headers=auth(ADMIN)).json()
        assert seen["claimed_amount"] == "700.00"
        assert seen["reviewed_by"] == ADMIN
        assert seen["reviewed_at"]

        rows = client.get("/api/scans", headers=auth(EMPLOYEE)).json()
        body = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE))
        detail = body.json()

        # The shape is the same one a pending claim has, to the key.
        assert set(rows[0]) == self.LIST_KEYS
        assert set(detail) == self.DETAIL_KEYS
        for key in (*self.FORBIDDEN, "reviewed_by", "reviewed_at"):
            assert key not in rows[0]
            assert key not in detail
        # The amount itself, not only the key it would arrive under.
        assert "700.00" not in body.text
        # What they ARE told: where their claim has got to.
        assert detail["review_status"] == "reviewed"

    def test_the_content_is_a_transcription_and_never_a_comparison(
        self, client: TestClient
    ) -> None:
        """The hard boundary, checked against the whole serialised body.

        A key check alone is not enough — a nested object carrying findings
        would satisfy one and still put the comparison on the wire.
        """
        saved = save(client, EMPLOYEE)
        body = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).text
        for key in self.COMPARISON_KEYS:
            assert f'"{key}"' not in body, f"{key!r} reached the submitter"
        detail = json.loads(body)
        assert set(detail["content"]) == {
            "prescription", "pharmacy_bill", "lab_bill", "billed_total", "currency",
        }
        assert set(detail["content"]["prescription"]) == {
            "prescriber", "clinic", "date", "patient_name", "patient_age",
            "patient_sex", "medicines", "investigations",
        }
        assert set(detail["content"]["pharmacy_bill"]) == {
            "name", "bill_no", "bill_date", "lines", "subtotal", "tax",
            "grand_total", "currency",
        }

    def test_a_billed_line_nobody_prescribed_looks_like_any_other(
        self, client: TestClient
    ) -> None:
        """The point of the boundary, stated as a test.

        Nothing on a transcribed line says whether it was matched, so an
        unprescribed medicine and a prescribed one are indistinguishable here.
        """
        saved = save(client, EMPLOYEE)
        detail = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).json()
        shapes = {tuple(sorted(line)) for line in detail["content"]["pharmacy_bill"]["lines"]}
        assert len(shapes) <= 1, "every billed line has the same fields"
        for line in detail["content"]["pharmacy_bill"]["lines"]:
            assert set(line) == {
                "item", "batch", "expiry", "pack", "quantity", "rate", "amount",
                "raw_text",
            }

    def test_the_total_is_the_documents_own_and_is_not_a_claim(
        self, client: TestClient
    ) -> None:
        """`billed_total` must never be an eligible or claimable figure."""
        saved = save(client, EMPLOYEE)
        detail = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).json()
        assert "billed_total" in detail["content"]
        for name in ("eligible_total", "claimed_amount", "supported_total"):
            assert name not in detail["content"]

    def test_the_admin_still_gets_the_whole_thing(self, client: TestClient) -> None:
        """The narrowing is by role, not a deletion."""
        saved = save(client, EMPLOYEE)
        detail = client.get(f"/api/scans/{saved['id']}", headers=auth(ADMIN)).json()
        assert detail["result"] == result_payload()
        assert detail["verdict"] == "mismatch"
        assert "readability" not in detail


class TestCertification:
    def test_a_submission_starts_uncertified_and_submitted(
        self, client: TestClient
    ) -> None:
        saved = save(client, EMPLOYEE)
        row = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).json()
        assert row["certified_by_employee"] is False
        assert row["certified_at"] is None
        assert row["review_status"] == "submitted"

    def test_certifying_records_who_and_when(self, client: TestClient) -> None:
        saved = save(client, EMPLOYEE)
        done = client.post(f"/api/scans/{saved['id']}/certify", headers=auth(EMPLOYEE))
        assert done.status_code == 200
        assert done.json()["certified_by_employee"] is True
        assert done.json()["certified_at"] is not None

    def test_only_the_submitter_may_certify(self, client: TestClient) -> None:
        """An attestation somebody else can make for you is not an attestation.

        Not even the admin — and the 404 is the same one a missing id gets, so
        probing tells a caller nothing.
        """
        saved = save(client, EMPLOYEE)
        refused = client.post(f"/api/scans/{saved['id']}/certify", headers=auth(ADMIN))
        assert refused.status_code == 404

    def test_certify_answers_with_the_same_shape_as_opening_the_claim(
        self, client: TestClient
    ) -> None:
        """It returned the summary, so the client replaced its state with an
        object that had no readability and no transcription, and the screen
        went blank. The two endpoints answer identically now."""
        saved = save(client, EMPLOYEE)
        opened = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).json()
        done = client.post(
            f"/api/scans/{saved['id']}/certify", headers=auth(EMPLOYEE)
        ).json()
        assert set(done) == set(opened)
        assert done["readability"] == opened["readability"]
        assert done["content"] == opened["content"]

    def test_the_first_attestation_is_the_one_that_stands(
        self, client: TestClient
    ) -> None:
        saved = save(client, EMPLOYEE)
        first = client.post(
            f"/api/scans/{saved['id']}/certify", headers=auth(EMPLOYEE)
        ).json()
        again = client.post(
            f"/api/scans/{saved['id']}/certify", headers=auth(EMPLOYEE)
        ).json()
        assert again["certified_at"] == first["certified_at"]


class TestAllFourDocumentsAreRecorded:
    """A submitter is shown what they did and did not attach.

    Only the two required filenames were stored, so a lab bill that was
    uploaded — and transcribed further down the same screen — was missing from
    the list of documents received.
    """

    def test_every_slot_comes_back(self, client: TestClient) -> None:
        saved = save(
            client, EMPLOYEE,
            prescription_filename="rx.png", bill_filename="bill.png",
            lab_report_filename="report.png", lab_bill_filename="lab.png",
        )
        row = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).json()
        assert row["prescription_filename"] == "rx.png"
        assert row["bill_filename"] == "bill.png"
        assert row["lab_report_filename"] == "report.png"
        assert row["lab_bill_filename"] == "lab.png"

    def test_an_optional_slot_left_empty_stays_empty_not_absent(
        self, client: TestClient
    ) -> None:
        """Empty is what the screen renders as "Not supplied". A missing key
        would render as nothing at all, which is a different statement."""
        saved = save(client, EMPLOYEE)
        row = client.get(f"/api/scans/{saved['id']}", headers=auth(EMPLOYEE)).json()
        assert row["lab_report_filename"] == ""
        assert row["lab_bill_filename"] == ""
