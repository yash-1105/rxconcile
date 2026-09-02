"""Stored pages: four documents, several pages each.

The two BLOB columns held one page each of two documents. That was the right
shape while a PDF meant its own first page and lab documents were never kept;
neither is true now.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pypdfium2
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine, select

from rxconcile.store import ScanPage, set_engine

EMPLOYEE = "employee@gmail.com"
ADMIN = "admin@gmail.com"


def png(size: tuple[int, int] = (400, 500)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def pdf(pages: int) -> bytes:
    doc = pypdfium2.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(595, 842)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A file-backed database per test.

    Not `sqlite://` — an in-memory database is per CONNECTION, so the schema
    created here is invisible to the one the request handler checks out.
    """
    from rxconcile.main import app

    engine = create_engine(f"sqlite:///{tmp_path / 'pages.db'}")
    SQLModel.metadata.create_all(engine)
    set_engine(engine)
    with TestClient(app) as test_client:
        yield test_client
    set_engine(None)


def token(client: TestClient, email: str) -> str:
    password = "admin123" if email == ADMIN else "employee123"
    response = client.post("/api/demo/session", json={"email": email, "password": password})
    return str(response.json()["token"])


def auth(client: TestClient, email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(client, email)}"}


def save(client: TestClient, *, report_pages: int = 3) -> int:
    payload = {
        "first_name": "Yash", "middle_name": "", "last_name": "",
        "employee_number": "EMP-4417",
        "prescription_filename": "rx.png", "bill_filename": "bill.png",
        "lab_report_filename": "report.pdf", "lab_bill_filename": "lb.png",
        "condition": "Diabetes", "description": None,
        "extraction_runs": 1,
        "result": {"verdict": "match", "processing_ms": 1},
    }
    import json as jsonlib

    response = client.post(
        "/api/scans",
        data={"payload": jsonlib.dumps(payload)},
        files={
            "prescription": ("rx.png", png(), "image/png"),
            "bill": ("bill.png", png(), "image/png"),
            "lab_report": ("report.pdf", pdf(report_pages), "application/pdf"),
            "lab_bill": ("lb.png", png(), "image/png"),
        },
        headers=auth(client, EMPLOYEE),
    )
    assert response.status_code == 200, response.text
    return int(response.json()["id"])


def test_every_page_of_every_document_is_stored(client: TestClient) -> None:
    scan_id = save(client, report_pages=3)
    manifest = client.get(f"/api/scans/{scan_id}/pages", headers=auth(client, EMPLOYEE)).json()
    by_slot: dict[str, list[int]] = {}
    for page in manifest["pages"]:
        by_slot.setdefault(page["slot"], []).append(page["page_no"])

    assert by_slot["prescription"] == [1]
    assert by_slot["pharmacy_bill"] == [1]
    assert by_slot["lab_bill"] == [1]
    # The point of the change: a three-page PDF stores three pages.
    assert by_slot["lab_report"] == [1, 2, 3]
    assert manifest["legacy_only"] is False


def test_a_page_comes_back_as_an_image(client: TestClient) -> None:
    scan_id = save(client)
    response = client.get(
        f"/api/scans/{scan_id}/page/lab_report/2", headers=auth(client, EMPLOYEE)
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert response.content[:2] == b"\xff\xd8"  # JPEG


def test_a_page_that_does_not_exist_is_404_not_a_different_page(
    client: TestClient
) -> None:
    """The old endpoint returned the BILL image for any unknown slot."""
    scan_id = save(client, report_pages=2)
    assert client.get(
        f"/api/scans/{scan_id}/page/lab_report/9", headers=auth(client, EMPLOYEE)
    ).status_code == 404
    assert client.get(
        f"/api/scans/{scan_id}/page/nonsense/1", headers=auth(client, EMPLOYEE)
    ).status_code == 404


def test_the_old_two_document_endpoint_cannot_serve_the_wrong_document(
    client: TestClient
) -> None:
    scan_id = save(client)
    got = client.get(f"/api/scans/{scan_id}/image/lab_report", headers=auth(client, EMPLOYEE))
    assert got.status_code == 404, "an unknown slot must not fall through to the bill"


class TestAccessIsUnchanged:
    def test_an_employee_sees_their_own_pages(self, client: TestClient) -> None:
        scan_id = save(client)
        assert client.get(
            f"/api/scans/{scan_id}/pages", headers=auth(client, EMPLOYEE)
        ).status_code == 200

    def test_a_reviewer_sees_them_too(self, client: TestClient) -> None:
        scan_id = save(client)
        assert client.get(
            f"/api/scans/{scan_id}/pages", headers=auth(client, ADMIN)
        ).status_code == 200

    def test_pages_carry_no_comparison(self, client: TestClient) -> None:
        """A page manifest is a list of pages. Nothing about the analysis."""
        scan_id = save(client)
        body = client.get(f"/api/scans/{scan_id}/pages", headers=auth(client, EMPLOYEE)).text
        for word in ("verdict", "finding", "discrepancy", "claimed", "eligible"):
            assert word not in body.lower()


def test_pages_are_the_preprocessed_ones(client: TestClient) -> None:
    """What the model saw, so a bounding box lands correctly."""
    scan_id = save(client)
    from rxconcile.store import engine

    with Session(engine()) as session:
        rows = session.exec(select(ScanPage).where(ScanPage.scan_id == scan_id)).all()
    assert rows, "pages must be stored"
    for row in rows:
        assert row.media_type == "image/jpeg", "preprocessing normalises to JPEG"
        assert row.width > 0 and row.height > 0
