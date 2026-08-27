"""Retry and model-fallback wrapper around Vertex ``generate_content``.

Two policies, deliberately distinct:

**Transport retry.** Up to :data:`MAX_ATTEMPTS` attempts per model with
exponential backoff plus jitter, on 429 (RESOURCE_EXHAUSTED) and 503
(UNAVAILABLE) only. Every other status fails immediately -- retrying a 400 or a
403 just delays the error.

**Model fallback.** If the primary model is still returning 429 after its
attempts are spent, the request is retried once against
``GEMINI_MODEL_QUOTA_FALLBACK`` (a same-tier Flash model) on the same endpoint.
A 503 does *not* trigger this: quota is model-scoped, availability is not.

There is deliberately **no regional fallback**. Gemini 3.x publisher models
resolve only on the ``global`` endpoint in this project -- ``us-central1``
returns 404 -- so a cross-region retry would convert a transient 429 into a hard
404. The global endpoint already routes across regions internally.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Final

from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from rxconcile.config import settings
from rxconcile.gcp import health
from rxconcile.gcp.client import get_client
from rxconcile.gcp.errors import VertexUnavailableError

logger: Final = logging.getLogger(__name__)

#: Attempts per model before giving up on it.
MAX_ATTEMPTS: Final[int] = 3

#: Quota exhaustion. The only status that triggers model fallback.
RESOURCE_EXHAUSTED: Final[int] = 429

#: Transient backend unavailability. Retried, but never switches model.
UNAVAILABLE: Final[int] = 503

RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({RESOURCE_EXHAUSTED, UNAVAILABLE})

_BACKOFF_BASE_SECONDS: Final[float] = 1.0
_BACKOFF_MAX_SECONDS: Final[float] = 30.0
_JITTER_RATIO: Final[float] = 0.25

#: Indirection so tests can run without real delays.
_sleep = time.sleep


class GenerationResult(BaseModel):
    """A successful generation, annotated with how it was obtained."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    response: types.GenerateContentResponse = Field(description="Raw SDK response.")
    model: str = Field(description="Model that actually served the request.")
    attempts: int = Field(description="Total attempts made across all models.")
    used_quota_fallback: bool = Field(
        description="True if the primary model was quota-exhausted and the fallback served."
    )

    @property
    def text(self) -> str:
        """Concatenated text parts of the response, empty string if none."""
        return self.response.text or ""


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter for a 1-indexed ``attempt``."""
    raw = _BACKOFF_BASE_SECONDS * float(2 ** (attempt - 1))
    capped = min(raw, _BACKOFF_MAX_SECONDS)
    jitter = capped * _JITTER_RATIO * random.random()
    return float(capped + jitter)


def _status_of(exc: genai_errors.APIError) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _attempt_model(
    model: str,
    contents: types.ContentListUnionDict,
    config: types.GenerateContentConfigOrDict | None,
    max_attempts: int,
) -> tuple[types.GenerateContentResponse | None, int, int | None]:
    """Try ``model`` up to ``max_attempts`` times.

    Returns ``(response, attempts_used, last_retryable_status)``. ``response`` is
    None if every attempt failed with a retryable status; non-retryable errors
    propagate.
    """
    client = get_client()
    last_status: int | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model=model, contents=contents, config=config
            )
        except genai_errors.APIError as exc:
            status = _status_of(exc)
            if status not in RETRYABLE_STATUSES:
                logger.error(
                    "model=%s attempt=%d non-retryable status=%s: %s",
                    model, attempt, status, exc,
                )
                raise
            last_status = status
            if attempt == max_attempts:
                logger.warning(
                    "model=%s exhausted %d attempts, last status=%s",
                    model, max_attempts, status,
                )
                break
            delay = _backoff_seconds(attempt)
            logger.warning(
                "model=%s attempt=%d/%d failed status=%s, retrying in %.2fs",
                model, attempt, max_attempts, status, delay,
            )
            _sleep(delay)
        else:
            logger.info("model=%s served request on attempt %d", model, attempt)
            return response, attempt, None

    return None, max_attempts, last_status


def generate_content(
    contents: types.ContentListUnionDict,
    *,
    model: str | None = None,
    config: types.GenerateContentConfigOrDict | None = None,
) -> GenerationResult:
    """Generate content, retrying transient failures and falling back on quota.

    Args:
        contents: Prompt payload, in any form the SDK accepts.
        model: Override the configured primary model.
        config: Optional generation config.

    Returns:
        A :class:`GenerationResult` naming the model that actually served it.

    Raises:
        VertexUnavailableError: retries and fallback were exhausted.
        google.genai.errors.APIError: any non-retryable API failure.
    """
    primary = model or settings.gemini_model
    fallback = settings.gemini_model_quota_fallback

    response, attempts, last_status = _attempt_model(
        primary, contents, config, MAX_ATTEMPTS
    )
    if response is not None:
        health.record_served(primary, used_quota_fallback=False)
        return GenerationResult(
            response=response, model=primary, attempts=attempts, used_quota_fallback=False
        )

    # Quota is model-scoped, so a same-tier model may still have headroom.
    # Availability (503) is not, so it does not justify switching models.
    if last_status == RESOURCE_EXHAUSTED and fallback != primary:
        logger.warning(
            "model=%s quota-exhausted after %d attempts; falling back to model=%s",
            primary, attempts, fallback,
        )
        fb_response, fb_attempts, fb_status = _attempt_model(
            fallback, contents, config, 1
        )
        total = attempts + fb_attempts
        if fb_response is not None:
            health.record_served(fallback, used_quota_fallback=True)
            return GenerationResult(
                response=fb_response,
                model=fallback,
                attempts=total,
                used_quota_fallback=True,
            )
        raise VertexUnavailableError(
            f"Both {primary!r} (status {last_status}) and fallback {fallback!r} "
            f"(status {fb_status}) failed after {total} attempts. The project is "
            "likely out of Vertex quota; check quotas for aiplatform.googleapis.com."
        )

    raise VertexUnavailableError(
        f"Model {primary!r} failed after {attempts} attempts with status "
        f"{last_status}. No model fallback was attempted because status "
        f"{last_status} is not quota exhaustion."
    )
