"""API surface. Extraction is always stubbed -- this suite never calls Gemini."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from rxconcile import main
from rxconcile.config import settings
from rxconcile.extract import _runner
from rxconcile.extract.dto import (
    BilledItemDTO,
    PharmacyBillDTO,
    PrescribedItemDTO,
    PrescriptionDTO,
)
from rxconcile.extract.errors import ExtractionError

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the extraction cache at a per-test directory.

    Test images are byte-identical, so a shared cache would serve the first
    test's result to every later one and the stubs would never be called.
    """
    from rxconcile.extract import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "extraction")


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(main.app) as test_client:
        yield test_client


def png_bytes(size: tuple[int, int] = (400, 300)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def rx_dto() -> PrescriptionDTO:
    return PrescriptionDTO(
        patient_name="R. Sharma",
        overall_legibility=0.9,
        items=[
            PrescribedItemDTO(
                raw_text="TAB DOLO 650", drug_name="Dolo", strength_value=650.0,
                strength_unit="mg", form="tablet", frequency_raw="1-0-1",
                duration_raw="x 5 days", duration_days=5, confidence=0.9,
            )
        ],
    )


def bill_dto() -> PharmacyBillDTO:
    return PharmacyBillDTO(
        patient_name="R. Sharma",
        items=[
            BilledItemDTO(
                raw_text="DOLO 650 TAB", drug_name="Dolo", strength_value=650.0,
                strength_unit="mg", form="tablet", quantity=10.0, pack_size="10'S",
                units_basis="unit", confidence=0.9,
            )
        ],
    )


class StubExtractor:
    """Replaces the single-run extractor. Records every call."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_next: list[Exception] = []

    def __call__(self, **kwargs: Any) -> Any:
        dto_type = kwargs["dto_type"]
        self.calls.append(kwargs["doc_type"])
        if self.fail_next:
            raise self.fail_next.pop(0)
        return rx_dto() if dto_type is PrescriptionDTO else bill_dto()


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> StubExtractor:
    extractor = StubExtractor()
    monkeypatch.setattr(_runner, "run_extraction", extractor)
    return extractor


def upload(name: str = "rx.png", mime: str = "image/png") -> tuple[str, bytes, str]:
    return (name, png_bytes(), mime)


# --------------------------------------------------------------------------
# health and samples
# --------------------------------------------------------------------------


def test_health_wraps_the_existing_snapshot(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"]
    assert "primary_model" in body
    assert "last_served_model" in body


def test_samples_listing(client: TestClient) -> None:
    response = client.get("/api/samples")
    assert response.status_code == 200
    samples = response.json()
    assert samples
    assert {"sample_id", "label", "prescription", "bill"} <= set(samples[0])


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------


def test_reconcile_returns_a_complete_result(
    client: TestClient, stub: StubExtractor
) -> None:
    response = client.post(
        "/api/reconcile",
        files={"prescription": upload(), "bill": upload("bill.png")},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Nothing stripped: the audit panel needs all of it.
    for key in (
        "verdict", "score", "findings", "matched_pairs", "unmatched_prescribed",
        "unmatched_billed", "prescription", "bill", "processing_ms", "review_summary",
    ):
        assert key in body, key
    # run_item_counts is the item count each run returned -- three runs, one item.
    assert body["prescription"]["run_item_counts"] == [1, 1, 1]
    assert len(body["prescription"]["run_item_counts"]) == 3
    assert "unstable_lines" in body["prescription"]
    assert "agreement" in body["prescription"]["items"][0]
    assert "units_basis" in body["bill"]["items"][0]


def test_default_runs_issue_six_model_calls(client: TestClient, stub: StubExtractor) -> None:
    """N=3 over two documents is six calls, not two."""
    client.post("/api/reconcile", files={"prescription": upload(), "bill": upload("b.png")})
    assert len(stub.calls) == 6
    assert stub.calls.count("prescription") == 3
    assert stub.calls.count("bill") == 3


def test_extract_endpoints(client: TestClient, stub: StubExtractor) -> None:
    rx = client.post("/api/extract/prescription", files={"file": upload()})
    assert rx.status_code == 200
    assert rx.json()["items"][0]["drug_name"] == "Dolo"
    bill = client.post("/api/extract/bill", files={"file": upload("b.png")})
    assert bill.status_code == 200
    assert bill.json()["items"][0]["item_id"] == "bill-01"


def test_timing_header_is_present(client: TestClient, stub: StubExtractor) -> None:
    response = client.post(
        "/api/reconcile", files={"prescription": upload(), "bill": upload("b.png")}
    )
    assert int(response.headers["X-Processing-Ms"]) >= 0
    assert response.json()["processing_ms"] >= 0


# --------------------------------------------------------------------------
# N=1
# --------------------------------------------------------------------------


def test_single_run_reports_null_agreement_not_one(
    client: TestClient, stub: StubExtractor
) -> None:
    response = client.post(
        "/api/reconcile",
        files={"prescription": upload(), "bill": upload("b.png")},
        data={"runs": "1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(stub.calls) == 2
    assert body["prescription"]["items"][0]["agreement"] is None
    assert body["prescription"]["run_item_counts"] == [1]


def test_single_run_review_summary_is_null_not_zero(
    client: TestClient, stub: StubExtractor
) -> None:
    """Zero would claim nothing needs review; one run cannot establish that."""
    response = client.post(
        "/api/reconcile",
        files={"prescription": upload(), "bill": upload("b.png")},
        data={"runs": "1"},
    )
    summary = response.json()["review_summary"]
    assert summary["agreement_measured"] is False
    assert summary["items_needing_review"] is None
    assert summary["fields_nulled_by_disagreement"] is None
    assert summary["unstable_line_count"] is None


def test_runs_out_of_range_is_rejected(client: TestClient, stub: StubExtractor) -> None:
    response = client.post(
        "/api/reconcile",
        files={"prescription": upload(), "bill": upload("b.png")},
        data={"runs": "0"},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_RUNS"
    assert stub.calls == []


# --------------------------------------------------------------------------
# upload validation
# --------------------------------------------------------------------------


def test_oversized_upload_is_rejected_before_any_model_call(
    client: TestClient, stub: StubExtractor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        type(settings), "max_upload_bytes", property(lambda _self: 100)
    )
    response = client.post(
        "/api/reconcile", files={"prescription": upload(), "bill": upload("b.png")}
    )
    assert response.status_code == 413
    body = response.json()
    assert body["error_code"] == "FILE_TOO_LARGE"
    assert body["hint"]
    assert stub.calls == [], "a model call was issued for an oversized upload"


def test_unsupported_mime_type_is_rejected(client: TestClient, stub: StubExtractor) -> None:
    response = client.post(
        "/api/reconcile",
        files={
            "prescription": ("notes.txt", b"hello", "text/plain"),
            "bill": upload("b.png"),
        },
    )
    assert response.status_code == 415
    assert response.json()["error_code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert stub.calls == []


def test_empty_file_is_rejected(client: TestClient, stub: StubExtractor) -> None:
    response = client.post(
        "/api/reconcile",
        files={"prescription": ("rx.png", b"", "image/png"), "bill": upload("b.png")},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "EMPTY_FILE"


def test_pdf_first_page_is_rendered(client: TestClient, stub: StubExtractor) -> None:
    import pypdfium2

    pdf = pypdfium2.PdfDocument.new()
    pdf.new_page(300, 400)
    buffer = io.BytesIO()
    pdf.save(buffer)
    response = client.post(
        "/api/extract/prescription",
        files={"file": ("rx.pdf", buffer.getvalue(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    assert stub.calls, "the rendered PDF page never reached extraction"


def test_corrupt_pdf_is_reported_not_crashed(client: TestClient, stub: StubExtractor) -> None:
    response = client.post(
        "/api/extract/prescription",
        files={"file": ("rx.pdf", b"%PDF-1.4 not really a pdf", "application/pdf")},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "UNREADABLE_IMAGE"


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


def test_extraction_failure_returns_an_actionable_hint(
    client: TestClient, stub: StubExtractor
) -> None:
    stub.fail_next = [ExtractionError("both attempts failed schema validation")]
    response = client.post("/api/extract/prescription", files={"file": upload()})
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "EXTRACTION_FAILED"
    assert "blurry" in body["hint"].lower()
    assert "retake" in body["hint"].lower()


def test_quota_exhaustion_names_the_models_tried(
    client: TestClient, stub: StubExtractor
) -> None:
    from rxconcile.gcp.errors import VertexUnavailableError

    stub.fail_next = [VertexUnavailableError("both models exhausted")]
    response = client.post("/api/extract/prescription", files={"file": upload()})
    assert response.status_code == 503
    body = response.json()
    assert body["error_code"] == "QUOTA_EXHAUSTED"
    assert settings.gemini_model in body["hint"]
    assert settings.gemini_model_quota_fallback in body["hint"]


def test_model_resolution_failure_points_at_list_models(
    client: TestClient, stub: StubExtractor
) -> None:
    from rxconcile.gcp.errors import ModelResolutionError

    stub.fail_next = [ModelResolutionError("gemini-3.1-pro-preview does not resolve")]
    response = client.post("/api/extract/prescription", files={"file": upload()})
    assert response.status_code == 503
    body = response.json()
    assert body["error_code"] == "MODEL_UNAVAILABLE"
    assert "make list-models" in body["hint"]
    assert "gemini-3.1-pro-preview" in body["message"]


def test_unknown_sample_id(client: TestClient) -> None:
    response = client.post("/api/reconcile/sample", json={"sample_id": "nope"})
    assert response.status_code == 404
    assert response.json()["error_code"] == "SAMPLE_NOT_FOUND"


# --------------------------------------------------------------------------
# serialisation of the awkward cases
# --------------------------------------------------------------------------


def test_inconclusive_verdict_serialises_score_as_null(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """None, never coerced to 0. Zero reads as 'measured, terrible'."""
    fixture = json.loads((FIXTURE_DIR / "03_illegible_prescription.json").read_text())

    async def fake_rx(*_args: Any, **_kwargs: Any) -> Any:
        from rxconcile.models import Prescription

        return Prescription.model_validate(fixture["prescription"])

    async def fake_bill(*_args: Any, **_kwargs: Any) -> Any:
        from rxconcile.models import PharmacyBill

        return PharmacyBill.model_validate(fixture["bill"])

    monkeypatch.setattr(main, "extract_prescription_async", fake_rx)
    monkeypatch.setattr(main, "extract_bill_async", fake_bill)

    response = client.post(
        "/api/reconcile", files={"prescription": upload(), "bill": upload("b.png")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "inconclusive"
    assert body["score"] is None
    assert '"score":null' in response.text.replace(" ", "")


def test_quantity_ambiguous_detail_reaches_the_wire(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """basis_method and both interpretations must survive serialisation."""
    from rxconcile.models import BilledItem, PharmacyBill, PrescribedItem, Prescription

    prescription = Prescription(
        overall_legibility=0.9,
        run_item_counts=[3, 3, 3],
        items=[
            PrescribedItem(
                item_id="rx-01", raw_text="TAB DOLO 650", drug_name="Dolo",
                strength_value=650.0, strength_unit="mg", form="tablet",
                frequency_raw="1-0-1", duration_raw="x 5 days", duration_days=5,
                dose_per_administration=1.0, confidence=0.9,
                agreement={"drug_name": 1.0},
            )
        ],
    )
    pharmacy = PharmacyBill(
        run_item_counts=[3, 3, 3],
        items=[
            BilledItem(
                item_id="bill-01", raw_text="DOLO 650", drug_name="Dolo",
                strength_value=650.0, strength_unit="mg", form="tablet",
                quantity=1.0, pack_size="10'S", unit_price=Decimal("178.00"),
                line_total=Decimal("178.00"), confidence=0.9, agreement={"drug_name": 1.0},
            )
        ],
    )

    async def fake_rx(*_a: Any, **_k: Any) -> Any:
        return prescription

    async def fake_bill(*_a: Any, **_k: Any) -> Any:
        return pharmacy

    monkeypatch.setattr(main, "extract_prescription_async", fake_rx)
    monkeypatch.setattr(main, "extract_bill_async", fake_bill)

    body = client.post(
        "/api/reconcile", files={"prescription": upload(), "bill": upload("b.png")}
    ).json()
    ambiguous = next(f for f in body["findings"] if f["rule_code"] == "QUANTITY_AMBIGUOUS")
    assert ambiguous["detail"]["basis_method"] == "price_inconclusive"
    assert ambiguous["detail"]["interpretations"]["as_units"]["billed_units"] == 1.0
    assert ambiguous["detail"]["interpretations"]["as_packs"]["billed_units"] == 10.0


# --------------------------------------------------------------------------
# concurrency and fallback under load
# --------------------------------------------------------------------------


def test_a_429_on_one_of_six_calls_still_completes_via_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six concurrent calls make a 429 more likely; the fallback must hold.

    Stubs the transport under the *real* retry wrapper, so the fallback path is
    exercised rather than mocked away.

`run_extraction` builds one GenerateContentConfig per call and reuses it across
    that call's retries, so its identity marks a single call's retry sequence.
    (Thread identity does not: asyncio.to_thread recycles pool threads across
    tasks.) The first call seen is designated unlucky and gets a 429 on every
    primary-model attempt; it must exhaust its retries and complete on the quota
    fallback while the other five succeed on the primary.
    """
    import threading

    from google.genai import errors as genai_errors
    from google.genai import types

    from rxconcile.gcp import retry

    monkeypatch.setattr(retry, "_sleep", lambda _seconds: None)
    lock = threading.Lock()
    state: dict[str, Any] = {"unlucky": None, "served": [], "rejections": 0}

    def sdk_response(text: str) -> types.GenerateContentResponse:
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model", parts=[types.Part.from_text(text=text)]
                    )
                )
            ]
        )

    class Models:
        def generate_content(
            self, *, model: str, contents: Any, config: Any
        ) -> types.GenerateContentResponse:
            call_id = id(config)
            with lock:
                if state["unlucky"] is None:
                    state["unlucky"] = call_id
                unlucky = state["unlucky"] == call_id
                if unlucky and model == settings.gemini_model:
                    state["rejections"] += 1
                    raise genai_errors.APIError(429, {"error": {"message": "quota"}})
                state["served"].append(model)
            payload = rx_dto() if config.response_schema is PrescriptionDTO else bill_dto()
            return sdk_response(payload.model_dump_json())

    class Client:
        models = Models()

    monkeypatch.setattr(retry, "get_client", lambda: Client())

    response = client.post(
        "/api/reconcile", files={"prescription": upload(), "bill": upload("b.png")}
    )
    assert response.status_code == 200, response.text

    # The unlucky call exhausted all three primary attempts...
    assert state["rejections"] == retry.MAX_ATTEMPTS
    # ...and was served by the quota fallback instead.
    assert settings.gemini_model_quota_fallback in state["served"]
    # The other five never needed it.
    assert state["served"].count(settings.gemini_model) == 5
    assert len(response.json()["prescription"]["items"]) == 1


def test_transient_429s_are_absorbed_by_retry_without_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scattered 429s across concurrent calls should not reach the fallback.

    Retry is per call, so a single rejection on each of several calls is
    absorbed. Switching model on the first 429 would change extraction behaviour
    mid-request for no reason.

    Rejections are keyed on call identity so that each affected call sees exactly
    one. Counting rejections globally would be racy: with backoff stubbed out, a
    single call can consume all of them and then legitimately fall back.
    """
    import threading

    from google.genai import errors as genai_errors
    from google.genai import types

    from rxconcile.gcp import retry

    monkeypatch.setattr(retry, "_sleep", lambda _seconds: None)
    lock = threading.Lock()
    state: dict[str, Any] = {"rejected_calls": set(), "served": [], "limit": 3}

    class Models:
        def generate_content(
            self, *, model: str, contents: Any, config: Any
        ) -> types.GenerateContentResponse:
            call_id = id(config)
            with lock:
                rejected = state["rejected_calls"]
                if call_id not in rejected and len(rejected) < state["limit"]:
                    rejected.add(call_id)
                    raise genai_errors.APIError(429, {"error": {"message": "quota"}})
                state["served"].append(model)
            payload = rx_dto() if config.response_schema is PrescriptionDTO else bill_dto()
            return types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(
                            role="model",
                            parts=[types.Part.from_text(text=payload.model_dump_json())],
                        )
                    )
                ]
            )

    class Client:
        models = Models()

    monkeypatch.setattr(retry, "get_client", lambda: Client())
    response = client.post(
        "/api/reconcile", files={"prescription": upload(), "bill": upload("b.png")}
    )
    assert response.status_code == 200, response.text
    assert len(state["rejected_calls"]) == 3, "the 429s were never issued"
    # Every call recovered on the primary model; the fallback was never needed.
    assert settings.gemini_model_quota_fallback not in state["served"]
    assert state["served"].count(settings.gemini_model) == 6
