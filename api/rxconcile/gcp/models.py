"""Model enumeration and boot-time resolution checks.

This mirrors ``make list-models`` (``api/scripts/list_models.sh``): both ask
Vertex which publisher models this project can actually reach. Published
documentation has lagged the real API, so the API is the only trustworthy source.
"""

from __future__ import annotations

import logging
from typing import Final

from rxconcile.config import settings
from rxconcile.gcp import health
from rxconcile.gcp.client import get_client
from rxconcile.gcp.errors import ModelResolutionError

logger: Final = logging.getLogger(__name__)


def list_available_models() -> frozenset[str]:
    """Return the bare IDs of every Google publisher model this project can reach.

    The SDK yields fully qualified names such as
    ``publishers/google/models/gemini-3.7-flash``; only the trailing segment is
    returned, matching the form used in ``.env``.
    """
    client = get_client()
    return frozenset(
        model.name.rsplit("/", 1)[-1]
        for model in client.models.list()
        if model.name is not None
    )


def list_gemini_models() -> tuple[str, ...]:
    """Sorted Gemini model IDs available to this project."""
    return tuple(sorted(m for m in list_available_models() if "gemini" in m))


def assert_models_resolve() -> tuple[str, ...]:
    """Verify every configured runtime model resolves, or raise.

    Call this once at startup. Preview model IDs are withdrawn without notice,
    and a model that vanished should fail the boot rather than the first
    request mid-demo.

    Returns the verified model IDs.

    Raises:
        ModelResolutionError: if any configured model is not reachable.
    """
    expected = settings.runtime_models
    available = list_available_models()
    missing = tuple(model for model in expected if model not in available)

    if missing:
        gemini = sorted(m for m in available if "gemini" in m)
        raise ModelResolutionError(
            f"Configured model(s) {list(missing)} do not resolve against project "
            f"{settings.gcp_project_id!r} at location {settings.gcp_location!r}. "
            "A Preview model ID may have been withdrawn, or the ID may be wrong. "
            f"Gemini models currently available: {gemini}. "
            "Run 'make list-models' and update .env."
        )

    health.record_verified(expected)
    logger.info("verified %d model(s) resolve: %s", len(expected), ", ".join(expected))
    return expected
