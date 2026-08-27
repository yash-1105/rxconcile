"""Runtime health state for the Google Cloud layer.

Holds the small amount of mutable process state a ``/health`` endpoint needs:
which models are configured, whether they were verified at boot, and which model
actually served the most recent request.

This module owns the state so that :mod:`rxconcile.gcp.models` and
:mod:`rxconcile.gcp.retry` can both write to it without importing each other.
"""

from __future__ import annotations

import threading
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from rxconcile.config import settings

_lock: Final[threading.Lock] = threading.Lock()

_models_verified: bool = False
_verified_models: tuple[str, ...] = ()
_last_served_model: str | None = None
_request_count: int = 0
_quota_fallback_count: int = 0


class HealthSnapshot(BaseModel):
    """Point-in-time view of the Vertex layer, safe to serialise to a caller."""

    model_config = ConfigDict(frozen=True)

    project_id: str = Field(description="Google Cloud project serving requests.")
    location: str = Field(description="Vertex endpoint location.")
    primary_model: str = Field(description="Configured primary extraction model.")
    quota_fallback_model: str = Field(
        description="Model used when the primary is quota-exhausted."
    )
    escalation_model: str = Field(
        description="Pro-tier model available for hard documents."
    )
    models_verified_at_startup: bool = Field(
        description="Whether every runtime model was confirmed to resolve at boot."
    )
    verified_models: tuple[str, ...] = Field(
        default=(), description="Models confirmed to resolve at boot."
    )
    last_served_model: str | None = Field(
        default=None,
        description="Model that served the most recent request; null before any request.",
    )
    request_count: int = Field(default=0, description="Successful requests served.")
    quota_fallback_count: int = Field(
        default=0,
        description="Requests served by the quota fallback model rather than the primary.",
    )

    @property
    def healthy(self) -> bool:
        """True when boot verification passed."""
        return self.models_verified_at_startup


def record_verified(models: tuple[str, ...]) -> None:
    """Mark boot-time model verification as passed."""
    global _models_verified, _verified_models
    with _lock:
        _models_verified = True
        _verified_models = models


def record_served(model: str, *, used_quota_fallback: bool) -> None:
    """Record that ``model`` successfully served a request."""
    global _last_served_model, _request_count, _quota_fallback_count
    with _lock:
        _last_served_model = model
        _request_count += 1
        if used_quota_fallback:
            _quota_fallback_count += 1


def reset() -> None:
    """Clear runtime state. Intended for tests."""
    global _models_verified, _verified_models, _last_served_model
    global _request_count, _quota_fallback_count
    with _lock:
        _models_verified = False
        _verified_models = ()
        _last_served_model = None
        _request_count = 0
        _quota_fallback_count = 0


def health_snapshot() -> HealthSnapshot:
    """Build a :class:`HealthSnapshot` from current process state."""
    with _lock:
        return HealthSnapshot(
            project_id=settings.gcp_project_id,
            location=settings.gcp_location,
            primary_model=settings.gemini_model,
            quota_fallback_model=settings.gemini_model_quota_fallback,
            escalation_model=settings.gemini_model_fallback,
            models_verified_at_startup=_models_verified,
            verified_models=_verified_models,
            last_served_model=_last_served_model,
            request_count=_request_count,
            quota_fallback_count=_quota_fallback_count,
        )
