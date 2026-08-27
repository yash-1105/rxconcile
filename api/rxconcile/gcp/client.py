"""Lazily constructed, cached google-genai client bound to Vertex AI.

Authentication is Application Default Credentials, always. There are no service
account key files in this project and none may be added: ADC keeps short-lived
credentials outside the repository, where they cannot be committed.

Run ``gcloud auth application-default login`` once, then
``gcloud auth application-default set-quota-project <project>``. Skipping the
quota-project step produces confusing 403s that name an unrelated project.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Final

from google import genai

from rxconcile.config import settings

logger: Final = logging.getLogger(__name__)

_KEYFILE_ENV_VAR: Final[str] = "GOOGLE_APPLICATION_CREDENTIALS"

_client: genai.Client | None = None
_lock: Final[threading.Lock] = threading.Lock()


def _build_client() -> genai.Client:
    if os.environ.get(_KEYFILE_ENV_VAR):
        raise RuntimeError(
            f"{_KEYFILE_ENV_VAR} is set. rxconcile is ADC-only and never loads "
            "service account key files. Unset it and run: "
            "gcloud auth application-default login"
        )
    logger.info(
        "constructing Vertex client project=%s location=%s",
        settings.gcp_project_id,
        settings.gcp_location,
    )
    return genai.Client(
        vertexai=True,
        project=settings.gcp_project_id,
        location=settings.gcp_location,
    )


def get_client() -> genai.Client:
    """Return the process-wide Vertex client, constructing it on first use.

    Construction is deferred so that importing rxconcile never performs network
    or credential discovery, and double-checked so concurrent first calls share
    one client.
    """
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = _build_client()
    return _client


def reset_client() -> None:
    """Discard the cached client. Intended for tests."""
    global _client
    with _lock:
        _client = None
