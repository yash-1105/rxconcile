"""FastAPI application.

Thin transport over the pipeline. All judgement lives in
:mod:`rxconcile.reconcile.engine`; this module accepts uploads, fans extraction
out concurrently, and serialises the result whole.

**Concurrency.** ``/api/reconcile`` at the default N=3 issues **six** model
calls. They are fanned out on both axes -- the runs within a document and the
two documents -- so all six are in flight at once and wall time is roughly one
call rather than six. Each call still passes through the retry and
quota-fallback wrapper independently.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.responses import Response

from rxconcile.config import settings
from rxconcile.extract import extract_bill_async, extract_prescription_async
from rxconcile.extract.errors import (
    ExtractionError,
    ImageTooLargeError,
    UnreadableImageError,
)
from rxconcile.gcp import health_snapshot
from rxconcile.gcp.errors import ModelResolutionError, VertexUnavailableError
from rxconcile.models import PharmacyBill, Prescription, ReconciliationResult
from rxconcile.reconcile import reconcile

logger: Final = logging.getLogger(__name__)

ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/jpg", "image/png", "image/webp", "application/pdf"}
)
PDF_MIME_TYPE: Final[str] = "application/pdf"

#: The Vite dev server. Nothing else may call this API from a browser.
ALLOWED_ORIGINS: Final[tuple[str, ...]] = ("http://localhost:5173",)

SAMPLES_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "samples"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ApiError(Exception):
    """An error with a machine-readable code and an actionable hint."""

    def __init__(
        self, *, status_code: int, error_code: str, message: str, hint: str
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.hint = hint

    def payload(self) -> dict[str, str]:
        return {"error_code": self.error_code, "message": self.message, "hint": self.hint}


def _unsupported_type(content_type: str | None) -> ApiError:
    return ApiError(
        status_code=415,
        error_code="UNSUPPORTED_MEDIA_TYPE",
        message=f"Unsupported file type {content_type!r}.",
        hint="Upload a JPEG, PNG, WebP or PDF. A phone photo saved as .jpg works.",
    )


def _too_large(size_bytes: int) -> ApiError:
    return ApiError(
        status_code=413,
        error_code="FILE_TOO_LARGE",
        message=(
            f"File is {size_bytes / 1_048_576:.1f} MB; the limit is "
            f"{settings.max_upload_mb} MB."
        ),
        hint=(
            "Retake the photo at a lower resolution, or export the PDF at reduced "
            f"quality, so the file is under {settings.max_upload_mb} MB."
        ),
    )


def _disagreement(document: str, run_item_counts: list[int], unstable: list[str]) -> ApiError:
    return ApiError(
        status_code=422,
        error_code="EXTRACTION_DISAGREEMENT",
        message=(
            f"The {document} was read differently on every attempt "
            f"(item counts {run_item_counts}), so no line could be confirmed."
        ),
        hint=(
            "This usually means the handwriting or photo quality is borderline. "
            "Retake the photo square-on in even light with the whole page in frame, "
            "and avoid shadows across the drug list."
        ),
    )


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

app = FastAPI(
    title="rxconcile",
    version="0.1.0",
    description=(
        "Reconciles a handwritten prescription against a pharmacy bill. "
        "Proof of concept, not a medical device. Reports document discrepancies "
        "only: no medical advice, no dosing recommendations, no clinical judgement."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def timing_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Record wall time and expose it as a response header.

    With the fan-out working, a six-call reconcile should land near single-call
    latency. If this header reads like six calls in series, the fan-out is broken.
    """
    started = time.monotonic()
    response = await call_next(request)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    response.headers["X-Processing-Ms"] = str(elapsed_ms)
    return response


@app.exception_handler(ApiError)
async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


@app.exception_handler(ImageTooLargeError)
async def image_too_large_handler(_request: Request, exc: ImageTooLargeError) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "error_code": "FILE_TOO_LARGE",
            "message": str(exc),
            "hint": f"Reduce the image below {settings.max_upload_mb} MB and retry.",
        },
    )


@app.exception_handler(UnreadableImageError)
async def unreadable_handler(_request: Request, exc: UnreadableImageError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error_code": "UNREADABLE_IMAGE",
            "message": str(exc),
            "hint": (
                "The file could not be decoded as an image. Re-export it as JPEG or "
                "PNG, or re-take the photo."
            ),
        },
    )


@app.exception_handler(ExtractionError)
async def extraction_error_handler(_request: Request, exc: ExtractionError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error_code": "EXTRACTION_FAILED",
            "message": str(exc),
            "hint": (
                "Image too blurry to transcribe. Retake in better light with the "
                "full page in frame, square-on and without shadows."
            ),
        },
    )


@app.exception_handler(ModelResolutionError)
async def model_resolution_handler(_request: Request, exc: ModelResolutionError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error_code": "MODEL_UNAVAILABLE",
            "message": str(exc),
            "hint": (
                "A configured model ID no longer resolves against this project -- "
                "Preview IDs are withdrawn without notice. Run `make list-models` to "
                "see what this project can actually reach, then update GEMINI_MODEL, "
                "GEMINI_MODEL_FALLBACK or GEMINI_MODEL_QUOTA_FALLBACK in .env."
            ),
        },
    )


@app.exception_handler(VertexUnavailableError)
async def vertex_unavailable_handler(
    _request: Request, exc: VertexUnavailableError
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error_code": "QUOTA_EXHAUSTED",
            "message": str(exc),
            "hint": (
                f"Both {settings.gemini_model} and the quota fallback "
                f"{settings.gemini_model_quota_fallback} were exhausted. Check the "
                "aiplatform.googleapis.com quota for this project, or retry with "
                "runs=1 to cut the call count per document from "
                f"{settings.extraction_runs} to 1."
            ),
        },
    )


# --------------------------------------------------------------------------
# Upload handling
# --------------------------------------------------------------------------


def _render_pdf_first_page(data: bytes) -> bytes:
    """Render page 1 of a PDF to PNG bytes."""
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(io.BytesIO(data))
        if len(document) == 0:
            raise UnreadableImageError("PDF contains no pages")
        page = document[0]
        # 200 DPI keeps thin handwriting strokes legible without bloating the upload.
        image = page.render(scale=200 / 72).to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except UnreadableImageError:
        raise
    except Exception as exc:  # pypdfium2 raises a variety of native errors
        raise UnreadableImageError(f"PDF could not be rendered: {exc}") from exc


async def read_upload(upload: UploadFile) -> bytes:
    """Validate and read an upload, rendering a PDF's first page to PNG.

    Size is checked **before** any model call, so an oversized file costs nothing.
    """
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_MIME_TYPES:
        raise _unsupported_type(upload.content_type)

    data = await upload.read()
    if not data:
        raise ApiError(
            status_code=422,
            error_code="EMPTY_FILE",
            message="The uploaded file is empty.",
            hint="Check the file transferred correctly and upload it again.",
        )
    if len(data) > settings.max_upload_bytes:
        raise _too_large(len(data))

    if content_type == PDF_MIME_TYPE:
        return _render_pdf_first_page(data)
    return data


def _resolved_runs(runs: int | None) -> int:
    if runs is None:
        return settings.extraction_runs
    if runs < 1 or runs > 9:
        raise ApiError(
            status_code=422,
            error_code="INVALID_RUNS",
            message=f"runs={runs} is out of range.",
            hint="Use a value between 1 and 9. 1 is cheapest; 3 is the default and "
            "the lowest that can measure agreement at all.",
        )
    return runs


def _guard_disagreement(document: Prescription | PharmacyBill, label: str) -> None:
    """Fail loudly when the runs could not agree on a single line."""
    if len(document.run_item_counts) > 1 and not document.items and document.unstable_lines:
        raise _disagreement(label, list(document.run_item_counts), list(document.unstable_lines))


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


class SampleSummary(BaseModel):
    """A bundled prescription/bill pair for one-click demos."""

    sample_id: str
    label: str
    prescription: str
    bill: str
    note: str | None = None


class SampleRequest(BaseModel):
    sample_id: str = Field(min_length=1)


SAMPLES: Final[tuple[SampleSummary, ...]] = (
    SampleSummary(
        sample_id="p3-dental",
        label="Dental prescription vs matching pharmacy bill",
        prescription="p3.jpg",
        bill="synthetic_bill_p3.png",
        note="Real prescription photo. The bill is synthetic, with planted "
        "discrepancies: a strength mismatch, a missing item and an unprescribed one.",
    ),
    SampleSummary(
        sample_id="synthetic-clean",
        label="Synthetic prescription vs synthetic bill",
        prescription="synthetic_prescription.png",
        bill="synthetic_bill.png",
        note="Both documents synthetic. Typed text, not handwriting.",
    ),
)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness and configuration, including which model last served a request."""
    return health_snapshot().model_dump()


@app.get("/api/samples")
async def list_samples() -> list[SampleSummary]:
    """Bundled sample pairs that exist on disk."""
    return [
        sample
        for sample in SAMPLES
        if (SAMPLES_DIR / sample.prescription).is_file()
        and (SAMPLES_DIR / sample.bill).is_file()
    ]


async def _reconcile_bytes(
    prescription_bytes: bytes, bill_bytes: bytes, runs: int
) -> ReconciliationResult:
    """Extract both documents concurrently, then reconcile.

    ``asyncio.gather`` over two documents, each itself gathering N runs, so all
    ``2 x N`` calls are in flight together.
    """
    started = time.monotonic()
    prescription, bill = await asyncio.gather(
        extract_prescription_async(prescription_bytes, runs=runs),
        extract_bill_async(bill_bytes, runs=runs),
    )
    _guard_disagreement(prescription, "prescription")
    _guard_disagreement(bill, "bill")
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return reconcile(prescription, bill, processing_ms=elapsed_ms)


@app.get("/api/samples/{sample_id}/image/{which}")
async def sample_image(sample_id: str, which: str) -> FileResponse:
    """Serve a sample's source image, so the viewer can show the actual page."""
    sample = next((s for s in SAMPLES if s.sample_id == sample_id), None)
    if sample is None or which not in {"prescription", "bill"}:
        raise ApiError(
            status_code=404,
            error_code="SAMPLE_NOT_FOUND",
            message=f"No {which!r} image for sample {sample_id!r}.",
            hint="Call GET /api/samples for the available sample_id values.",
        )
    filename = sample.prescription if which == "prescription" else sample.bill
    path = SAMPLES_DIR / filename
    if not path.is_file():
        raise ApiError(
            status_code=404,
            error_code="SAMPLE_FILE_MISSING",
            message=f"{filename} is missing from samples/.",
            hint="Check the samples/ directory in the repository is intact.",
        )
    return FileResponse(path)


@app.post("/api/reconcile")
async def reconcile_endpoint(
    prescription: UploadFile = File(..., description="Prescription image or PDF."),
    bill: UploadFile = File(..., description="Pharmacy bill image or PDF."),
    runs: int | None = Form(default=None, description="Extraction runs per document."),
) -> ReconciliationResult:
    """Reconcile a prescription against a bill. Returns the complete result."""
    run_count = _resolved_runs(runs)
    prescription_bytes = await read_upload(prescription)
    bill_bytes = await read_upload(bill)
    return await _reconcile_bytes(prescription_bytes, bill_bytes, run_count)


@app.post("/api/reconcile/sample")
async def reconcile_sample(request: SampleRequest, runs: int | None = None) -> ReconciliationResult:
    """Run the pipeline over a bundled sample pair."""
    sample = next((s for s in SAMPLES if s.sample_id == request.sample_id), None)
    if sample is None:
        raise ApiError(
            status_code=404,
            error_code="SAMPLE_NOT_FOUND",
            message=f"No sample named {request.sample_id!r}.",
            hint="Call GET /api/samples for the available sample_id values.",
        )
    prescription_path = SAMPLES_DIR / sample.prescription
    bill_path = SAMPLES_DIR / sample.bill
    if not prescription_path.is_file() or not bill_path.is_file():
        raise ApiError(
            status_code=404,
            error_code="SAMPLE_FILE_MISSING",
            message=f"Sample {sample.sample_id!r} is listed but its files are missing.",
            hint="Check the samples/ directory in the repository is intact.",
        )
    return await _reconcile_bytes(
        prescription_path.read_bytes(), bill_path.read_bytes(), _resolved_runs(runs)
    )


@app.post("/api/extract/prescription")
async def extract_prescription_endpoint(
    file: UploadFile = File(..., description="Prescription image or PDF."),
    runs: int | None = Form(default=None),
) -> Prescription:
    """Extract a prescription without reconciling it."""
    data = await read_upload(file)
    document = await extract_prescription_async(data, runs=_resolved_runs(runs))
    _guard_disagreement(document, "prescription")
    return document


@app.post("/api/extract/bill")
async def extract_bill_endpoint(
    file: UploadFile = File(..., description="Pharmacy bill image or PDF."),
    runs: int | None = Form(default=None),
) -> PharmacyBill:
    """Extract a pharmacy bill without reconciling it."""
    data = await read_upload(file)
    document = await extract_bill_async(data, runs=_resolved_runs(runs))
    _guard_disagreement(document, "bill")
    return document


__all__ = ["app"]
