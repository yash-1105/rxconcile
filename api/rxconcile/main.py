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
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, delete, select
from starlette.responses import Response

from rxconcile.config import settings
from rxconcile.demo_auth import DemoUser, issue_token, user_from_token, verify_credentials
from rxconcile.export import ExportContext, build_json, build_pdf, build_xlsx
from rxconcile.extract import extract_bill_async, extract_prescription_async
from rxconcile.extract.errors import (
    ExtractionError,
    ImageTooLargeError,
    UnreadableImageError,
)
from rxconcile.extract.preprocess import prepare_image
from rxconcile.gcp import health_snapshot
from rxconcile.gcp.errors import ModelResolutionError, VertexUnavailableError
from rxconcile.models import PharmacyBill, Prescription, ReconciliationResult
from rxconcile.normalize import lab_panels
from rxconcile.normalize.drug_dictionary import load_entries
from rxconcile.reconcile import reconcile
from rxconcile.store import ScanRecord, get_session, summarise

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
        sample_id="sample-01",
        label="Clean matching pair",
        prescription="sample-01-prescription.png",
        bill="sample-01-bill.png",
        note="Regression coverage. Em-dash sig notation, 'x 5 days' durations, "
        "\"10'S\" packs. Every line should reconcile.",
    ),
    SampleSummary(
        sample_id="sample-02",
        label="Strength mismatch and an unprescribed antibiotic",
        prescription="sample-02-prescription.png",
        bill="sample-02-bill.png",
        note="Regression coverage. En-dash sig notation, '5/7' duration, '1x10' "
        "packs. Telma billed at 80mg against 40mg prescribed, plus a Levoflox line "
        "with no prescription behind it.",
    ),
    SampleSummary(
        sample_id="sample-03",
        label="Brand substitution",
        prescription="sample-03-prescription.png",
        bill="sample-03-bill.png",
        note="Regression coverage. Mixed dash forms, 'STRIP OF 10' packs, a 'NOS' "
        "quantity column, mixed-case drug names. Dolo dispensed as Calpol and Pan "
        "as Pantocid: same salts, legal substitution.",
    ),
    SampleSummary(
        sample_id="p3-dental",
        label="Real dental prescription vs matching pharmacy bill",
        prescription="p3.jpg",
        bill="synthetic_bill_p3.png",
        note="Real prescription photograph. The bill is synthetic, with planted "
        "discrepancies: a strength mismatch, a missing item and an unprescribed one.",
    ),
    SampleSummary(
        sample_id="synthetic-clean",
        label="Synthetic prescription vs synthetic bill",
        prescription="synthetic_prescription.png",
        bill="synthetic_bill.png",
        note="Typed text, not handwriting. Exercises the pipeline, not accuracy.",
    ),
)


# --------------------------------------------------------------------------
# Demo identity
#
# NOT AUTHENTICATION. See rxconcile/demo_auth.py -- credentials and signing
# secret are both committed. The only property this buys is that the server
# decides who the caller is from a token it issued, rather than believing a role
# the caller asserts. A caller-supplied role would be no filter at all.
# --------------------------------------------------------------------------


class DemoSessionRequest(BaseModel):
    email: str
    password: str


class DemoSessionResponse(BaseModel):
    token: str
    email: str
    name: str
    employee_number: str
    role: str


def current_user(authorization: str | None = Header(default=None)) -> DemoUser:
    """Resolve the caller from the bearer token. The role is never taken from input."""
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    user = user_from_token(token) if token else None
    if user is None:
        raise ApiError(
            status_code=401,
            error_code="NOT_SIGNED_IN",
            message="No valid demo session was presented.",
            hint="Sign in on the demo screen again; the session ends when the tab closes.",
        )
    return user


@app.post("/api/demo/session")
async def demo_session(request: DemoSessionRequest) -> DemoSessionResponse:
    """Exchange demo credentials for a token bound to the email."""
    user = verify_credentials(request.email, request.password)
    if user is None:
        raise ApiError(
            status_code=401,
            error_code="BAD_DEMO_CREDENTIALS",
            message="Those demo credentials were not recognised.",
            hint="Use one of the fill-in buttons on the sign-in screen.",
        )
    return DemoSessionResponse(
        token=issue_token(user.email),
        email=user.email,
        name=user.name,
        employee_number=user.employee_number,
        role=user.role,
    )


# --------------------------------------------------------------------------
# Scan history
# --------------------------------------------------------------------------


class ScanCreate(BaseModel):
    """What the client supplies when saving a completed reconciliation.

    Deliberately absent: user_email and role. Both are taken from the token, so
    a caller cannot file a scan under someone else or claim to be an admin.
    """

    employee_name: str = Field(min_length=1)
    employee_number: str = Field(min_length=1)
    prescription_filename: str = ""
    bill_filename: str = ""
    extraction_runs: int = 0
    result: dict[str, Any]


class ScanSummary(BaseModel):
    """A history row. The full result is fetched only when a row is opened."""

    id: int
    created_at: str
    employee_name: str
    employee_number: str
    user_email: str
    role: str
    prescription_filename: str
    bill_filename: str
    verdict: str
    discrepancy_count: int
    critical_count: int
    warning_count: int
    checks_unavailable_count: int
    #: Reimbursement supported by the prescription. Derived from result_json on
    #: read rather than stored, so it can never drift from the result.
    eligible_total: str = "0"
    currency: str = "INR"
    processing_ms: int
    extraction_runs: int


class ScanDetail(ScanSummary):
    result: dict[str, Any]


def _summary(record: ScanRecord) -> ScanSummary:
    # Derived on read from the stored result, never written independently, so a
    # summary column cannot drift from the result it describes.
    money: dict[str, Any] = {}
    try:
        money = json.loads(record.result_json).get("reimbursement") or {}
    except ValueError:
        money = {}
    return ScanSummary(
        id=record.id or 0,
        created_at=record.created_at.isoformat(),
        employee_name=record.employee_name,
        employee_number=record.employee_number,
        user_email=record.user_email,
        role=record.role,
        prescription_filename=record.prescription_filename,
        bill_filename=record.bill_filename,
        verdict=record.verdict,
        discrepancy_count=record.discrepancy_count,
        critical_count=record.critical_count,
        warning_count=record.warning_count,
        checks_unavailable_count=record.checks_unavailable_count,
        eligible_total=str(money.get("eligible_total", "0")),
        currency=str(money.get("currency", "INR")),
        processing_ms=record.processing_ms,
        extraction_runs=record.extraction_runs,
    )


def _prepared(raw: bytes | None) -> bytes | None:
    """Preprocess a page the same way extraction did, or give up quietly.

    The PREPROCESSED bytes are what gets stored: bounding boxes are normalised
    against those dimensions, so a highlight only lands correctly on the image
    the model actually saw. A page that will not preprocess must never cost a
    caller their saved result, so failure returns None.
    """
    if not raw:
        return None
    try:
        return prepare_image(raw).data
    except Exception:  # noqa: BLE001 - storing pages is best-effort
        logger.warning("could not preprocess a page for storage", exc_info=True)
        return None


@app.post("/api/scans")
async def create_scan(
    payload_json: str = Form(..., alias="payload"),
    prescription: UploadFile | None = File(default=None),
    bill: UploadFile | None = File(default=None),
    sample_id: str | None = Form(default=None),
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> ScanSummary:
    """Save a completed reconciliation, with the pages it was run against.

    Multipart rather than JSON because the source pages travel with it. They
    are optional: a save must not fail for want of an image. When a bundled
    sample was used the client sends ``sample_id`` instead of the files, and
    the pages are read from disk here.
    """
    try:
        payload = ScanCreate.model_validate_json(payload_json)
    except ValueError as exc:
        raise ApiError(
            status_code=422,
            error_code="INVALID_PAYLOAD",
            message="The scan payload could not be read.",
            hint="Send the ScanCreate object as a JSON string in the 'payload' field.",
        ) from exc

    rx_bytes = await prescription.read() if prescription is not None else None
    bill_bytes = await bill.read() if bill is not None else None
    if sample_id and not (rx_bytes or bill_bytes):
        sample = next((s for s in SAMPLES if s.sample_id == sample_id), None)
        if sample is not None:
            rx_bytes = (SAMPLES_DIR / sample.prescription).read_bytes()
            bill_bytes = (SAMPLES_DIR / sample.bill).read_bytes()

    counts = summarise(payload.result)
    record = ScanRecord(
        employee_name=payload.employee_name,
        employee_number=payload.employee_number,
        user_email=user.email,
        role=user.role,
        prescription_filename=payload.prescription_filename,
        bill_filename=payload.bill_filename,
        verdict=str(payload.result.get("verdict", "unknown")),
        result_json=json.dumps(payload.result),
        prescription_image=_prepared(rx_bytes),
        bill_image=_prepared(bill_bytes),
        processing_ms=int(payload.result.get("processing_ms") or 0),
        extraction_runs=payload.extraction_runs,
        **counts,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return _summary(record)


@app.get("/api/scans")
async def list_scans(
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[ScanSummary]:
    """List scans. An employee sees only their own; an admin sees every record.

    The role comes from the token, never from the request, so narrowing it is
    not something the caller can opt out of.
    """
    statement = select(ScanRecord).order_by(col(ScanRecord.created_at).desc())
    if user.role != "admin":
        statement = statement.where(ScanRecord.user_email == user.email)
    return [_summary(record) for record in session.exec(statement).all()]


@app.get("/api/scans/{scan_id}")
async def get_scan(
    scan_id: int,
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> ScanDetail:
    """One scan in full, including the stored result."""
    record = session.get(ScanRecord, scan_id)
    if record is None or (user.role != "admin" and record.user_email != user.email):
        # Same response either way: an employee probing ids learns nothing about
        # whether a record belongs to someone else.
        raise ApiError(
            status_code=404,
            error_code="SCAN_NOT_FOUND",
            message=f"No scan with id {scan_id}.",
            hint="Open it from the History screen, which lists the scans you can see.",
        )
    summary = _summary(record)
    return ScanDetail(**summary.model_dump(), result=json.loads(record.result_json))


EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "pdf": ("application/pdf", "pdf"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "json": ("application/json", "json"),
}


def _export_context(record: ScanRecord) -> ExportContext:
    """Rebuild a report's inputs from a stored scan.

    The stored blob is validated here rather than trusted: a record written
    before a schema addition is missing fields, and pydantic fills those with
    the same defaults a fresh result would carry.
    """
    try:
        result = ReconciliationResult.model_validate(json.loads(record.result_json))
    except ValueError as exc:
        raise ApiError(
            status_code=422,
            error_code="EXPORT_UNAVAILABLE",
            message="This record cannot be exported: its stored result no longer validates.",
            hint="Re-run the reconciliation to produce an exportable record.",
        ) from exc
    return ExportContext(
        result=result,
        employee_name=record.employee_name,
        employee_number=record.employee_number,
        created_at=record.created_at,
        prescription_filename=record.prescription_filename,
        bill_filename=record.bill_filename,
        scan_id=record.id,
        extraction_runs=record.extraction_runs,
        prescription_image=record.prescription_image,
        bill_image=record.bill_image,
    )


def _owned_scan(scan_id: int, user: DemoUser, session: Session) -> ScanRecord:
    record = session.get(ScanRecord, scan_id)
    if record is None or (user.role != "admin" and record.user_email != user.email):
        # Same response either way: an employee probing ids learns nothing
        # about whether a record belongs to someone else.
        raise ApiError(
            status_code=404,
            error_code="SCAN_NOT_FOUND",
            message=f"No scan with id {scan_id}.",
            hint="Call GET /api/scans for the records visible to this account.",
        )
    return record


@app.get("/api/scans/{scan_id}/image/{which}")
async def scan_image(
    scan_id: int,
    which: str,
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    """A stored source page, as the extractor saw it after preprocessing."""
    record = _owned_scan(scan_id, user, session)
    data = record.prescription_image if which == "prescription" else record.bill_image
    if which not in {"prescription", "bill"} or data is None:
        raise ApiError(
            status_code=404,
            error_code="IMAGE_NOT_STORED",
            message="That page was not stored with this scan.",
            hint="Scans recorded before source pages were kept have no image to show.",
        )
    return Response(content=data, media_type=record.image_media_type)


@app.get("/api/scans/{scan_id}/export.{fmt}")
async def export_scan(
    scan_id: int,
    fmt: str,
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    """Export one scan as PDF, Excel or JSON.

    Every format carries the same disclaimer the screen does, and every one
    reports what could NOT be checked. A report outlives the session it came
    from; omitting that would let a later reader assume everything was
    examined.
    """
    if fmt not in EXPORTS:
        raise ApiError(
            status_code=404,
            error_code="UNKNOWN_FORMAT",
            message=f"{fmt!r} is not an export format.",
            hint=f"Use one of: {', '.join(sorted(EXPORTS))}.",
        )
    record = _owned_scan(scan_id, user, session)
    context = _export_context(record)
    builder = {"pdf": build_pdf, "xlsx": build_xlsx, "json": build_json}[fmt]
    media_type, suffix = EXPORTS[fmt]
    stamp = record.created_at.strftime("%Y%m%d-%H%M")
    filename = f"rxconcile-{record.id}-{stamp}.{suffix}"
    return Response(
        content=builder(context),
        media_type=media_type,
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/api/scans/{scan_id}")
async def delete_scan(
    scan_id: int,
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    """Delete a scan. Admin only."""
    if user.role != "admin":
        raise ApiError(
            status_code=403,
            error_code="NOT_PERMITTED_IN_DEMO",
            message="Only the admin demo account can delete scans.",
            hint="Sign in with admin@gmail.com to remove a record.",
        )
    record = session.get(ScanRecord, scan_id)
    if record is None:
        raise ApiError(
            status_code=404,
            error_code="SCAN_NOT_FOUND",
            message=f"No scan with id {scan_id}.",
            hint="It may already have been deleted.",
        )
    session.exec(delete(ScanRecord).where(col(ScanRecord.id) == scan_id))
    session.commit()
    return {"deleted": scan_id}


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


class DictionaryDrug(BaseModel):
    """One brand as the dictionary screen shows it."""

    brand_name: str
    salt_composition: str
    common_strengths: list[str]
    form: str
    therapeutic_class: str
    schedule: str


class DictionaryPanel(BaseModel):
    """One lab panel and the analytes a bill itemises it into."""

    name: str
    components: list[str]
    written_as: list[str]


class DictionaryResponse(BaseModel):
    """Both reference tables, served from the files the engine itself reads.

    Deliberately not duplicated into the frontend: a drifting copy of reference
    data is worse than a missing screen, because it looks authoritative while
    disagreeing with what the matcher actually did.
    """

    #: Reproduced from the module docstrings so the screen cannot fall out of
    #: step with the warning the code itself carries.
    warning: str
    drugs: list[DictionaryDrug]
    panels: list[DictionaryPanel]
    therapeutic_classes: list[str]
    schedules: list[str]


DICTIONARY_WARNING: Final[str] = (
    "Illustrative proof-of-concept data, not a validated drug database or laboratory "
    "reference. These entries were hand-compiled to exercise brand-to-salt resolution "
    "and panel decomposition on realistic Indian documents. They have not been verified "
    "against any regulatory source, the strengths listed are indicative rather than "
    "exhaustive, the schedule classifications are approximate, and panel compositions "
    "vary between laboratories. Do not use this data to make any clinical, dispensing "
    "or billing decision."
)


@app.get("/api/dictionary")
async def dictionary() -> DictionaryResponse:
    """The reference data the engine matches against, read from its own files."""
    entries = load_entries()
    drugs = [
        DictionaryDrug(
            brand_name=entry.brand_name,
            salt_composition=entry.salt_composition,
            common_strengths=list(entry.common_strengths),
            form=entry.form,
            therapeutic_class=entry.therapeutic_class,
            schedule=entry.schedule,
        )
        for entry in entries
    ]

    written: dict[str, list[str]] = {}
    for alias, canonical in lab_panels.PANEL_ALIASES.items():
        written.setdefault(canonical, []).append(alias)
    panels = [
        DictionaryPanel(
            name=name,
            components=list(components),
            written_as=sorted(written.get(name, [])),
        )
        for name, components in lab_panels.PANELS.items()
    ]

    return DictionaryResponse(
        warning=DICTIONARY_WARNING,
        drugs=drugs,
        panels=panels,
        therapeutic_classes=sorted({d.therapeutic_class for d in drugs if d.therapeutic_class}),
        schedules=sorted({d.schedule for d in drugs if d.schedule}),
    )


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
