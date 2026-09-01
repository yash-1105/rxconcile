"""Lazily constructed, cached google-genai client bound to Vertex AI.

Two authentication paths, and no third:

* **Locally, Application Default Credentials** -- as this project always has.
  Run ``gcloud auth application-default login`` once, then
  ``gcloud auth application-default set-quota-project <project>``. Skipping the
  quota-project step produces confusing 403s naming an unrelated project.
* **Deployed, a service account key in ``GOOGLE_APPLICATION_CREDENTIALS_JSON``**
  -- the JSON itself, parsed in memory and handed straight to the client.

What is still forbidden, per hard rule 9, is a key FILE. That is why
``GOOGLE_APPLICATION_CREDENTIALS`` remains rejected on sight: it names a path on
disk. The variable this module honours carries the material instead, so there is
no file to commit and none to leak, and nothing here ever writes one.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Final

import google.auth
from google import genai
from google.auth.exceptions import DefaultCredentialsError
from google.oauth2 import service_account

from rxconcile.config import ConfigError, service_account_info, settings

logger: Final = logging.getLogger(__name__)

_KEYFILE_ENV_VAR: Final[str] = "GOOGLE_APPLICATION_CREDENTIALS"

#: Vertex needs the broad cloud scope; a service account key carries no scopes
#: of its own, so one must be named when the credentials are built.
_SCOPES: Final[tuple[str, ...]] = ("https://www.googleapis.com/auth/cloud-platform",)

_client: genai.Client | None = None
_lock: Final[threading.Lock] = threading.Lock()


def resolve_credentials() -> service_account.Credentials | None:
    """Whatever this environment authenticates with, or a loud failure.

    Returns None when Application Default Credentials are available, because
    that is what ``genai.Client`` wants in order to discover them itself.

    Called once at startup rather than on the first extraction. A deployment
    with no usable credentials should die while it is being deployed, not hours
    later on somebody's first upload -- by then the failure looks like a broken
    feature rather than a missing environment variable.
    """
    info = service_account_info()
    if info is not None:
        try:
            # google-auth ships this constructor untyped, so the result is Any
            # and the call trips --strict. Annotated rather than relaxing the
            # setting: the looseness stays on this one line.
            built: service_account.Credentials = (
                service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
                    info, scopes=list(_SCOPES)
                )
            )
            return built
        except ValueError as exc:
            raise ConfigError(
                "GOOGLE_APPLICATION_CREDENTIALS_JSON holds a key that google-auth "
                f"could not load: {exc}. The most common cause is a private_key "
                "whose newlines were lost in transit -- paste the file's JSON "
                "verbatim rather than reformatting it."
            ) from exc

    try:
        google.auth.default(scopes=list(_SCOPES))
    except DefaultCredentialsError as exc:
        # Both paths named, because which one applies depends on where this is
        # running and the reader is the only one who knows which.
        raise ConfigError(
            "No Google credentials are available. Two ways to supply them:\n"
            "  locally  - gcloud auth application-default login\n"
            "  deployed - set GOOGLE_APPLICATION_CREDENTIALS_JSON to the whole "
            "service account key JSON\n"
            f"Application Default Credentials were tried and failed: {exc}"
        ) from exc
    return None


def _build_client() -> genai.Client:
    if os.environ.get(_KEYFILE_ENV_VAR):
        raise RuntimeError(
            f"{_KEYFILE_ENV_VAR} is set. rxconcile is ADC-only and never loads "
            "service account key files. Unset it and run: "
            "gcloud auth application-default login"
        )
    credentials = resolve_credentials()
    logger.info(
        "constructing Vertex client project=%s location=%s auth=%s",
        settings.gcp_project_id,
        settings.gcp_location,
        "service account" if credentials is not None else "ADC",
    )
    # Omitted rather than passed as None when absent: that is the signal for
    # google-genai to run its own ADC discovery, which is the local path.
    if credentials is None:
        return genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.gcp_location,
        )
    return genai.Client(
        vertexai=True,
        project=settings.gcp_project_id,
        location=settings.gcp_location,
        credentials=credentials,
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
