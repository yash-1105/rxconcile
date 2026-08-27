"""Shared extraction machinery for prescriptions and bills.

Both document types follow the same path -- prepare image, consult cache, call
the model with a response schema, validate, retry once on a schema failure --
so that path lives here rather than being duplicated and drifting.

Also home to the two conversions that Python owns rather than the model:
identifier assignment and date resolution.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final, TypeVar

from google.genai import types
from pydantic import BaseModel, ValidationError

from rxconcile.config import settings
from rxconcile.extract import cache
from rxconcile.extract.errors import ExtractionError
from rxconcile.extract.preprocess import PreparedImage
from rxconcile.extract.prompts import PROMPT_VERSION, schema_retry_suffix
from rxconcile.gcp import generate_content

logger: Final = logging.getLogger(__name__)

DTO = TypeVar("DTO", bound=BaseModel)

#: One retry after a schema-validation failure, then give up.
MAX_SCHEMA_ATTEMPTS: Final[int] = 2

#: Sampling temperature for extraction.
#:
#: Deliberately NOT 0.0. Self-consistency across N runs is the reliability
#: signal, and near-deterministic sampling would return N near-identical answers
#: -- manufacturing the appearance of agreement and reproducing exactly the
#: false reassurance the model's own confidence score already provides. The
#: variance is the measurement; it must not be tuned away.
#: See docs/DESIGN_DECISIONS.md section 2.
EXTRACTION_TEMPERATURE: Final[float] = 0.3

_ISO_FORMATS: Final[tuple[str, ...]] = ("%Y-%m-%d", "%Y/%m/%d")


def assign_ids(prefix: str, count: int) -> list[str]:
    """Return ``count`` identifiers -- ``rx-01``, ``rx-02``, ... -- in document order.

    Identifiers are assigned here, never by the model. Model-generated ids
    collide or skip, and the domain uniqueness validator would then reject
    otherwise-good extractions.
    """
    return [f"{prefix}-{index:02d}" for index in range(1, count + 1)]


def clamp_unit(value: float | None) -> float:
    """Clamp a confidence-style score into 0-1.

    A model returning 1.2 is a cosmetic slip, not a reason to burn the single
    retry, so it is corrected rather than rejected.
    """
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def to_decimal(value: float | None) -> Decimal | None:
    """Convert a JSON number to Decimal without inheriting binary float error."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def resolve_date(raw: str | None) -> tuple[date | None, str | None]:
    """Resolve a verbatim date string, or refuse to.

    Returns ``(date, warning)``. An ambiguous handwritten date such as
    ``03/04/26`` -- where both components could be the day -- comes back
    ``(None, warning)`` rather than resolved to a guess. Resolving it would
    silently invent a fact, and a wrong date on a prescription is a real error.

    A date is accepted only when it is unambiguous: ISO form, a written-out
    month, or a numeric form where one component exceeds 12.
    """
    if raw is None:
        return None, None
    text = raw.strip()
    if not text:
        return None, None

    for fmt in _ISO_FORMATS:
        try:
            return datetime.strptime(text, fmt).date(), None
        except ValueError:
            pass

    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(text, fmt).date(), None
        except ValueError:
            pass

    parts = [chunk for chunk in _split_numeric(text) if chunk]
    if len(parts) == 3 and all(chunk.isdigit() for chunk in parts):
        first, second, third = (int(chunk) for chunk in parts)
        year = _expand_year(third)
        if year is None:
            return None, (
                f"Date {raw!r} could not be resolved: the year is ambiguous. "
                "It was not guessed."
            )
        if first > 12 and second <= 12:
            return _safe_date(year, second, first, raw)
        if second > 12 and first <= 12:
            return _safe_date(year, first, second, raw)
        if first <= 12 and second <= 12:
            return None, (
                f"Date {raw!r} is ambiguous: both {first} and {second} could be the "
                "month, so it could not be resolved without guessing. Left as null."
            )

    return None, f"Date {raw!r} could not be interpreted and was left as null."


def _split_numeric(text: str) -> list[str]:
    out: list[str] = []
    current = ""
    for char in text:
        if char.isdigit():
            current += char
        else:
            out.append(current)
            current = ""
    out.append(current)
    return out


def _expand_year(value: int) -> int | None:
    if value >= 1900:
        return value
    if 0 <= value <= 99:
        # Two-digit years on a prescription are current-century in practice.
        return 2000 + value
    return None


def _safe_date(year: int, month: int, day: int, raw: str) -> tuple[date | None, str | None]:
    try:
        return date(year, month, day), None
    except ValueError:
        return None, f"Date {raw!r} is not a valid calendar date and was left as null."


def run_extraction(
    *,
    dto_type: type[DTO],
    instruction: str,
    image: PreparedImage,
    doc_type: str,
    model: str | None = None,
    use_cache: bool = True,
) -> DTO:
    """Extract ``dto_type`` from ``image``, retrying once on a schema failure.

    Raises:
        ExtractionError: both attempts failed validation, or the response was
            empty. Never returns a partially populated object.
    """
    chosen_model = model or settings.gemini_model
    key = cache.cache_key(
        image_sha256=image.sha256,
        doc_type=doc_type,
        model=chosen_model,
        prompt_version=PROMPT_VERSION,
    )

    if use_cache:
        cached = cache.load(key)
        if cached is not None:
            try:
                return dto_type.model_validate(cached)
            except ValidationError as exc:
                # Schema changed since the entry was written; treat as a miss.
                logger.warning("cache entry %s no longer validates: %s", key[:12], exc)

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=dto_type,
        temperature=EXTRACTION_TEMPERATURE,
    )
    prompt = instruction
    last_error: str | None = None

    for attempt in range(1, MAX_SCHEMA_ATTEMPTS + 1):
        parts: list[types.PartUnionDict] = [
            types.Part.from_bytes(data=image.data, mime_type=image.mime_type),
            types.Part.from_text(text=prompt),
        ]
        result = generate_content(parts, model=chosen_model, config=config)
        text = result.text.strip()
        if not text:
            last_error = "model returned an empty response"
            logger.warning("attempt %d/%d: %s", attempt, MAX_SCHEMA_ATTEMPTS, last_error)
        else:
            try:
                dto = dto_type.model_validate_json(text)
            except ValidationError as exc:
                last_error = str(exc)
                logger.warning(
                    "attempt %d/%d failed schema validation: %s",
                    attempt, MAX_SCHEMA_ATTEMPTS, last_error,
                )
            else:
                logger.info(
                    "extracted %s from %s via %s on attempt %d",
                    doc_type, image.sha256[:12], result.model, attempt,
                )
                if use_cache:
                    payload: dict[str, Any] = dto.model_dump(mode="json")
                    cache.store(key, payload)
                return dto

        if attempt < MAX_SCHEMA_ATTEMPTS:
            prompt = instruction + schema_retry_suffix(last_error or "unknown error")

    raise ExtractionError(
        f"{doc_type} extraction failed schema validation on both attempts. "
        f"Last error:\n{last_error}\n"
        "No partial object is returned: a half-populated document is "
        "indistinguishable from a real one downstream."
    )


def collect_runs(
    *,
    dto_type: type[DTO],
    instruction: str,
    image: PreparedImage,
    doc_type: str,
    runs: int,
    model: str | None = None,
) -> list[DTO]:
    """Extract ``runs`` independent times, for consensus resolution.

    Per-run caching is deliberately bypassed: replaying one cached answer N
    times would produce perfect agreement from a single observation. The caller
    caches the resolved document instead.

    Raises:
        ExtractionError: if any run fails after its retry. A missing run would
            silently change the agreement denominator.
    """
    return [
        run_extraction(
            dto_type=dto_type,
            instruction=instruction,
            image=image,
            doc_type=doc_type,
            model=model,
            use_cache=False,
        )
        for _ in range(runs)
    ]


async def collect_runs_async(
    *,
    dto_type: type[DTO],
    instruction: str,
    image: PreparedImage,
    doc_type: str,
    runs: int,
    model: str | None = None,
) -> list[DTO]:
    """Extract ``runs`` times **concurrently**, for consensus resolution.

    The SDK is synchronous, so each run goes to a worker thread and all of them
    are awaited together. Wall time is therefore roughly one call, not N.

    Each run still goes through :func:`run_extraction`, so the retry and
    quota-fallback wrapper applies per call. Concurrency makes a 429 *more*
    likely, not less, and each call falls back independently.
    """
    tasks = [
        asyncio.to_thread(
            run_extraction,
            dto_type=dto_type,
            instruction=instruction,
            image=image,
            doc_type=doc_type,
            model=model,
            use_cache=False,
        )
        for _ in range(runs)
    ]
    return list(await asyncio.gather(*tasks))
