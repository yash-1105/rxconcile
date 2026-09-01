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
import datetime as dt
import io
import json
import logging
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
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
from rxconcile.models import (
    PharmacyBill,
    Prescription,
    ReconciliationResult,
    Submission,
)
from rxconcile.normalize import lab_panels
from rxconcile.normalize.drug_dictionary import load_entries
from rxconcile.reconcile import reconcile
from rxconcile.reconcile.history import (
    HistoryScope,
    PriorCourse,
    PriorLine,
    PriorScan,
)
from rxconcile.reconcile.readability import (
    DocumentReadability,
    readability_of,
    unavailable,
)
from rxconcile.store import EmployeeAllowance, ScanRecord, get_session, summarise
from rxconcile.store.allowance import AllowanceView, view_for, year_label

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
        "as Pantocid: same salts, legal substitution. The Pan line is also "
        "prescribed as a capsule and billed as a tablet, which is a genuine form "
        "difference and the reason this pair reads as a mismatch.",
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


def optional_user(authorization: str | None = Header(default=None)) -> DemoUser | None:
    """The caller if signed in, None if not.

    Reconciliation itself never required a session, and adding one for the
    history checks would break that. Signed out, the history checks simply do
    not run rather than running against everything.
    """
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    return user_from_token(token) if token else None


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

    first_name: str = Field(min_length=1)
    middle_name: str = ""
    last_name: str = ""
    employee_number: str = Field(min_length=1)
    prescription_filename: str = ""
    bill_filename: str = ""
    condition: str | None = None
    description: str | None = None
    extraction_runs: int = 0
    #: Per-line accept/reject decisions, keyed by row. Stored verbatim.
    decisions: dict[str, Any] = Field(default_factory=dict)
    #: The claim the reviewer saw and approved. Sent by the client because it
    #: is derived from the same rows the tables render -- recomputing it here
    #: from a second implementation is exactly how it would drift from what was
    #: on screen when it was approved.
    claimed_amount: str = "0"
    result: dict[str, Any]


class ScanSummary(BaseModel):
    """A history row. The full result is fetched only when a row is opened."""

    id: int
    created_at: str
    employee_name: str
    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""
    employee_number: str
    review_status: str = "submitted"
    certified_by_employee: bool = False
    certified_at: str | None = None
    user_email: str
    role: str
    prescription_filename: str
    bill_filename: str
    condition: str | None = None
    description: str | None = None
    claimed_amount: str = "0"
    allowance_year: str = ""
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


class EmployeeScanSummary(BaseModel):
    """What an employee is told about their own submission.

    An ALLOW-LIST, deliberately, rather than the reviewer's model with fields
    blanked. A field added to `ScanSummary` later cannot leak into this one by
    default -- and a leak here would be silent, which is the failure mode worth
    designing against. `tests/test_scans.py` asserts the exact key set.

    Absent, not zeroed: verdict, every discrepancy count, the claim amount,
    every reimbursement figure, the decisions, the technical fields and the
    result itself. An employee submits; they do not review.
    """

    id: int
    created_at: str
    employee_name: str
    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""
    employee_number: str
    condition: str | None = None
    description: str | None = None
    prescription_filename: str = ""
    bill_filename: str = ""
    review_status: str = "submitted"
    certified_by_employee: bool = False
    certified_at: str | None = None


class EmployeeScanDetail(EmployeeScanSummary):
    #: Whether each uploaded document could be READ. Never a finding: a
    #: perfectly legible bill with a real discrepancy on it is not something to
    #: re-photograph, and the discrepancy is not theirs to see.
    readability: list[DocumentReadability] = Field(default_factory=list)


class ScanDetail(ScanSummary):
    result: dict[str, Any]
    #: The accept/reject decisions as they were last saved. Returned so that
    #: re-opening a scan shows what was decided rather than silently reverting
    #: to defaults -- a decision that does not survive the page is not a record.
    decisions: dict[str, Any] = Field(default_factory=dict)


def _employee_summary(record: ScanRecord) -> EmployeeScanSummary:
    """The submitter's own view of their submission. Nothing derived from the
    reconciliation appears here -- see EmployeeScanSummary."""
    return EmployeeScanSummary(
        id=record.id or 0,
        created_at=record.created_at.isoformat(),
        employee_name=record.employee_name,
        first_name=record.first_name,
        middle_name=record.middle_name,
        last_name=record.last_name,
        employee_number=record.employee_number,
        condition=record.condition,
        description=record.description,
        prescription_filename=record.prescription_filename,
        bill_filename=record.bill_filename,
        review_status=record.review_status,
        certified_by_employee=record.certified_by_employee,
        certified_at=(
            record.certified_at.isoformat() if record.certified_at is not None else None
        ),
    )


def _stored_result(record: ScanRecord) -> ReconciliationResult:
    """The stored blob, validated rather than trusted.

    A record written before a schema addition is missing fields, and pydantic
    fills those with the same defaults a fresh result would carry.
    """
    return ReconciliationResult.model_validate(json.loads(record.result_json))


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
        first_name=record.first_name,
        middle_name=record.middle_name,
        last_name=record.last_name,
        employee_number=record.employee_number,
        review_status=record.review_status,
        certified_by_employee=record.certified_by_employee,
        certified_at=(
            record.certified_at.isoformat() if record.certified_at is not None else None
        ),
        user_email=record.user_email,
        role=record.role,
        prescription_filename=record.prescription_filename,
        bill_filename=record.bill_filename,
        condition=record.condition,
        description=record.description,
        claimed_amount=str(record.claimed_amount),
        allowance_year=record.allowance_year,
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
        employee_name=" ".join(
            part for part in
            (payload.first_name, payload.middle_name, payload.last_name) if part.strip()
        ),
        first_name=payload.first_name,
        middle_name=payload.middle_name,
        last_name=payload.last_name,
        employee_number=payload.employee_number,
        user_email=user.email,
        role=user.role,
        prescription_filename=payload.prescription_filename,
        bill_filename=payload.bill_filename,
        condition=payload.condition,
        description=payload.description,
        decisions_json=json.dumps(payload.decisions),
        claimed_amount=Decimal(payload.claimed_amount or "0"),
        allowance_year=year_label(dt.date.today()),
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
) -> list[ScanSummary] | list[EmployeeScanSummary]:
    """List scans. An employee sees only their own; an admin sees every record.

    An employee also gets a NARROWER SHAPE, not the same rows with the analysis
    blanked: no verdict, no counts, no amounts. They submit; they do not review.

    The role comes from the token, never from the request, so narrowing it is
    not something the caller can opt out of.
    """
    statement = select(ScanRecord).order_by(col(ScanRecord.created_at).desc())
    if user.role != "admin":
        statement = statement.where(ScanRecord.user_email == user.email)
    records = list(session.exec(statement).all())
    if user.role != "admin":
        return [_employee_summary(record) for record in records]
    return [_summary(record) for record in records]


@app.post("/api/scans/{scan_id}/certify")
async def certify_scan(
    scan_id: int,
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> EmployeeScanSummary:
    """The employee's attestation that the documents are genuine and theirs.

    Recorded after the run rather than before it. The reconciliation is
    expensive, and losing it because somebody closed the tab before ticking a
    box would be a poor trade for a stricter ordering.

    Owner only: an attestation somebody else can make on your behalf is not an
    attestation.
    """
    record = session.get(ScanRecord, scan_id)
    if record is None or record.user_email != user.email:
        raise ApiError(
            status_code=404,
            error_code="SCAN_NOT_FOUND",
            message=f"No scan with id {scan_id}.",
            hint="Only the account that submitted a claim can certify it.",
        )
    # Never re-stamped. The first attestation is the one that was made.
    if not record.certified_by_employee:
        record.certified_by_employee = True
        record.certified_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        session.add(record)
        session.commit()
        session.refresh(record)
    return _employee_summary(record)


@app.get("/api/scans/{scan_id}")
async def get_scan(
    scan_id: int,
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> ScanDetail | EmployeeScanDetail:
    """One scan. In full for an admin; what they submitted for an employee.

    The employee's shape carries no reconciliation result at all -- not the
    findings, not the verdict, not a figure derived from either. What it does
    carry is whether each document could be READ, because that is the one thing
    they can act on and the one thing that otherwise fails silently.
    """
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
    if user.role != "admin":
        try:
            readability = readability_of(_stored_result(record))
        except ValueError:
            # An older record whose blob no longer validates. The submission
            # still exists and must still open; we simply cannot say what was
            # legible about it.
            readability = unavailable()
        return EmployeeScanDetail(
            **_employee_summary(record).model_dump(), readability=readability,
        )
    summary = _summary(record)
    try:
        stored = json.loads(record.decisions_json)
    except ValueError:
        stored = {}
    return ScanDetail(
        **summary.model_dump(),
        result=json.loads(record.result_json),
        decisions=stored if isinstance(stored, dict) else {},
    )


class AllowanceUpdate(BaseModel):
    """Set one employee's annual allowance."""

    employee_number: str = Field(min_length=1)
    employee_name: str = ""
    annual_amount: Decimal = Field(gt=0)


class DecisionUpdate(BaseModel):
    """Revised per-line decisions for a stored scan."""

    decisions: dict[str, Any] = Field(default_factory=dict)
    claimed_amount: str = "0"


@app.get("/api/allowance")
async def list_allowances(
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[AllowanceView]:
    """Allowance and balance per employee, narrowed by the same role rule.

    Built from the employees who actually appear in the scans this account can
    see, so an employee cannot learn who else exists by reading their balance.
    """
    statement = select(ScanRecord)
    if user.role != "admin":
        statement = statement.where(ScanRecord.user_email == user.email)
    names: dict[str, str] = {}
    for record in session.exec(statement).all():
        names.setdefault(record.employee_number, record.employee_name)
    return [
        view_for(session, number, employee_name=name)
        for number, name in sorted(names.items())
    ]


def _readable_numbers(user: DemoUser, session: Session) -> set[str]:
    """The employee numbers this account is allowed to see a balance for.

    An employee number is typed by hand on every scan, so an account may have
    several. The set is the numbers on the scans this account can see, plus its
    own — the same rule the scan list uses, so the two cannot disagree.
    """
    statement = select(ScanRecord).where(ScanRecord.user_email == user.email)
    numbers = {record.employee_number for record in session.exec(statement).all()}
    numbers.add(user.employee_number)
    return numbers


@app.get("/api/allowance/{employee_number}")
async def get_allowance(
    employee_number: str,
    exclude_scan_id: int | None = None,
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> AllowanceView:
    """One employee's allowance. ``exclude_scan_id`` leaves a scan out of used-so-far.

    Role-filtered. Without this an employee could read any colleague's annual
    allowance, spending and scan count by typing their employee number — the
    same thing the scan endpoints are careful never to leak.
    """
    if user.role != "admin" and employee_number not in _readable_numbers(user, session):
        # Same response whether or not that employee exists, so probing numbers
        # teaches nothing.
        raise ApiError(
            status_code=404,
            error_code="ALLOWANCE_NOT_FOUND",
            message=f"No allowance visible for {employee_number}.",
            hint="An account can only see the allowance behind its own scans.",
        )
    return view_for(session, employee_number, exclude_scan_id=exclude_scan_id)


@app.put("/api/allowance")
async def set_allowance(
    payload: AllowanceUpdate,
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> AllowanceView:
    """Set an employee's annual allowance. Admin only."""
    if user.role != "admin":
        raise ApiError(
            status_code=403,
            error_code="NOT_PERMITTED",
            message="Only an admin account may change an allowance.",
            hint="Sign in with the admin demo account.",
        )
    row = session.get(EmployeeAllowance, payload.employee_number)
    if row is None:
        row = EmployeeAllowance(employee_number=payload.employee_number)
    row.employee_name = payload.employee_name or row.employee_name
    row.annual_amount = payload.annual_amount
    session.add(row)
    session.commit()
    return view_for(session, payload.employee_number)


@app.patch("/api/scans/{scan_id}/decisions")
async def update_decisions(
    scan_id: int,
    payload: DecisionUpdate,
    user: DemoUser = Depends(current_user),
    session: Session = Depends(get_session),
) -> ScanSummary:
    """Revise the accept/reject decisions on a stored scan."""
    record = _owned_scan(scan_id, user, session)
    record.decisions_json = json.dumps(payload.decisions)
    record.claimed_amount = Decimal(payload.claimed_amount or "0")
    # Stamped from the scan's OWN date, not today's. A record written before
    # allowance years existed carries a blank one, and an amount in a blank year
    # counts against nothing -- the claim would be recorded and then silently
    # ignored by every balance on the system. Backfilled the first time anybody
    # decides on it, into the year the scan actually belongs to.
    if not record.allowance_year:
        record.allowance_year = year_label(record.created_at.date())
    session.add(record)
    session.commit()
    session.refresh(record)
    return _summary(record)


EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "pdf": ("application/pdf", "pdf"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "json": ("application/json", "json"),
}


def _export_context(record: ScanRecord, session: Session) -> ExportContext:
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
    try:
        decisions = json.loads(record.decisions_json)
    except ValueError:
        decisions = {}
    # Used-so-far EXCLUDES this scan, so the report reads the way the screen
    # did: what was drawn before this claim, then this claim beside it.
    allowance = view_for(
        session, record.employee_number, employee_name=record.employee_name,
        exclude_scan_id=record.id,
    )
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
        decisions=decisions if isinstance(decisions, dict) else {},
        claimed_amount=record.claimed_amount,
        annual_amount=allowance.annual_amount,
        used_amount=allowance.used,
        allowance_year=allowance.year,
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
    context = _export_context(record, session)
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
    prescription_bytes: bytes,
    bill_bytes: bytes,
    runs: int,
    history: tuple[list[PriorScan], HistoryScope] | None = None,
    lab_bill_bytes: bytes | None = None,
    submission: Submission | None = None,
) -> ReconciliationResult:
    """Extract both documents concurrently, then reconcile.

    ``asyncio.gather`` over two documents, each itself gathering N runs, so all
    ``2 x N`` calls are in flight together.
    """
    started = time.monotonic()
    jobs: list[Any] = [
        extract_prescription_async(prescription_bytes, runs=runs),
        extract_bill_async(bill_bytes, runs=runs),
    ]
    if lab_bill_bytes is not None:
        jobs.append(extract_bill_async(lab_bill_bytes, runs=runs))
    extracted = await asyncio.gather(*jobs)
    prescription, bill = extracted[0], extracted[1]
    _guard_disagreement(prescription, "prescription")
    _guard_disagreement(bill, "bill")

    if lab_bill_bytes is not None:
        # A separate lab bill is folded into the same PharmacyBill so the lab
        # comparison sees one set of billed tests. Its ids are re-issued to keep
        # them unique within the merged document, which the identity rule
        # requires; the raw text is untouched.
        lab_bill = extracted[2]
        _guard_disagreement(lab_bill, "bill")
        merged = list(bill.tests)
        for offset, test in enumerate(lab_bill.tests, start=len(merged) + 1):
            merged.append(test.model_copy(update={"item_id": f"billtest-{offset:02d}"}))
        bill = bill.model_copy(update={"tests": merged})
        # Kept before it is lost. Only `.tests` survives the merge, so the lab
        # bill's own read state has to be carried out separately or nothing can
        # ever tell the employee their lab bill was the unreadable one.
        submission = (submission or Submission()).model_copy(update={
            "lab_bill_supplied": True,
            "lab_bill_tests_read": len(lab_bill.tests),
            "lab_bill_unstable": len(set(lab_bill.run_item_counts)) > 1,
            "lab_bill_warnings": list(lab_bill.warnings),
        })
    elapsed_ms = int((time.monotonic() - started) * 1000)
    priors, scope = history if history is not None else (None, None)
    return reconcile(
        prescription, bill, processing_ms=elapsed_ms, priors=priors, history_scope=scope,
        submission=submission,
    )


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


#: The screen shows a single line. The full caveat still lives where an
#: engineer will meet it -- the docstrings on drug_dictionary and lab_panels --
#: and is not repeated at a client.
DICTIONARY_WARNING: Final[str] = "Reference data for demonstration."


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


def _prior_from_record(record: ScanRecord) -> PriorScan | None:
    """Reduce a stored scan to what the history checks read.

    Built from ``result_json`` rather than from summary columns, so it cannot
    drift from the result it describes. That means loading each blob, which is
    fine at demo volume and would want indexed columns at scale.
    """
    try:
        payload = json.loads(record.result_json)
    except ValueError:
        return None
    bill = payload.get("bill") or {}
    prescription = payload.get("prescription") or {}

    salts: dict[str, str] = {
        entry["item_id"]: entry["salt"]
        for entry in payload.get("canonical") or []
        if entry.get("side") == "prescription" and entry.get("salt")
    }
    courses: list[PriorCourse] = []
    for item in prescription.get("items") or []:
        # The canonical match is the reliable source; the transcribed salt is
        # the fallback for records written before it was reported.
        salt = salts.get(item.get("item_id", "")) or item.get("salt")
        if salt:
            courses.append(PriorCourse(salt=salt, duration_days=item.get("duration_days")))

    lines = [
        PriorLine(
            name=line.get("drug_name") or line.get("test_name") or line.get("raw_text") or "",
            line_total=line.get("line_total"),
        )
        for line in [*(bill.get("items") or []), *(bill.get("tests") or [])]
    ]

    return PriorScan(
        scan_id=record.id or 0,
        created_at=record.created_at,
        employee_name=record.employee_name,
        pharmacy_name=bill.get("pharmacy_name"),
        pharmacy_licence_no=bill.get("pharmacy_licence_no"),
        bill_no=bill.get("bill_no"),
        bill_date=bill.get("bill_date"),
        patient_name=bill.get("patient_name"),
        grand_total=bill.get("grand_total"),
        lines=tuple(lines),
        courses=tuple(courses),
    )


def _load_history(user: DemoUser, session: Session) -> tuple[list[PriorScan], HistoryScope]:
    """Prior scans this account may see, and a statement of that limitation.

    Narrowed HERE, by the same role rule the listing uses. An employee's
    duplicate check must not reveal that another account's scans exist, so the
    engine is handed only what this account can already read, and the scope
    travels into every finding.
    """
    statement = select(ScanRecord).order_by(col(ScanRecord.created_at).desc())
    if user.role != "admin":
        statement = statement.where(ScanRecord.user_email == user.email)
    records = list(session.exec(statement).all())
    priors = [prior for prior in map(_prior_from_record, records) if prior is not None]
    return priors, HistoryScope(
        scans_compared=len(priors),
        role=user.role,
        limited_to_own_scans=user.role != "admin",
    )


@app.post("/api/reconcile")
async def reconcile_endpoint(
    prescription: UploadFile = File(..., description="One file with all prescriptions."),
    bill: UploadFile = File(..., description="One file with all pharmacy bills."),
    lab_report: UploadFile | None = File(default=None, description="Lab reports. Optional."),
    lab_bill: UploadFile | None = File(default=None, description="Lab bills. Optional."),
    condition: str | None = Form(default=None, description="Condition being treated."),
    description: str | None = Form(default=None, description="Operator's notes. Optional."),
    runs: int | None = Form(default=None, description="Extraction runs per document."),
    user: DemoUser | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> ReconciliationResult:
    """Reconcile a prescription against a bill. Returns the complete result.

    Four documents, two of them required. A lab bill, where supplied, feeds the
    lab comparison; where it is not, the engine is TOLD so rather than having to
    infer it from what the extraction happened to find.

    History checks run only for a signed-in account, against the scans that
    account may already see.
    """
    run_count = _resolved_runs(runs)
    prescription_bytes = await read_upload(prescription)
    bill_bytes = await read_upload(bill)
    lab_bill_bytes = await read_upload(lab_bill) if lab_bill is not None else None
    # Lab reports are kept with the scan for the record. Nothing is extracted
    # from them: no rule reads a report, and inventing one here would be a
    # behaviour nobody asked for.
    lab_report_supplied = lab_report is not None
    if lab_report is not None:
        await read_upload(lab_report)

    submission = Submission(
        condition=(condition or "").strip() or None,
        description=(description or "").strip() or None,
        prescription_supplied=True,
        pharmacy_bill_supplied=True,
        lab_report_supplied=lab_report_supplied,
        lab_bill_supplied=lab_bill_bytes is not None,
    )
    history = _load_history(user, session) if user is not None else None
    return await _reconcile_bytes(
        prescription_bytes, bill_bytes, run_count, history, lab_bill_bytes, submission,
    )


@app.post("/api/reconcile/sample")
async def reconcile_sample(
    request: SampleRequest,
    runs: int | None = None,
    user: DemoUser | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> ReconciliationResult:
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
    history = _load_history(user, session) if user is not None else None
    return await _reconcile_bytes(
        prescription_path.read_bytes(), bill_path.read_bytes(), _resolved_runs(runs), history,
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
