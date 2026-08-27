"""Application configuration, loaded from the repo-root ``.env``.

Settings are validated and frozen at import time. Importing this module raises
immediately if the environment is unusable, so a misconfigured deployment dies
at boot rather than at first request.

No model ID is hardcoded here. All three runtime models are required keys with
no Python default, so the only place a model ID lives is ``.env``. A default
baked into this module would be invisible when the ID is withdrawn, and Preview
IDs are withdrawn without notice.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_ENV_FILE: Final[Path] = _REPO_ROOT / ".env"

#: Vertex resolves Gemini 3.x publisher models only on the ``global`` endpoint.
#: ``us-central1`` returns 404 for ``gemini-3.7-flash`` in this project, so a
#: regional value is a configuration error, not a fallback.
SUPPORTED_LOCATIONS: Final[frozenset[str]] = frozenset({"global"})

#: Setting this points google-auth at a service account key file. rxconcile is
#: ADC-only by policy, so its presence is rejected rather than honoured.
_KEYFILE_ENV_VAR: Final[str] = "GOOGLE_APPLICATION_CREDENTIALS"


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


class Settings(BaseSettings):
    """Typed view of ``.env``.

    Environment variables are matched case-insensitively against field names,
    so ``GCP_PROJECT_ID`` populates :attr:`gcp_project_id`. A real environment
    variable always wins over a value in the ``.env`` file.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    gcp_project_id: str = Field(
        ...,
        min_length=1,
        description="Google Cloud project that owns the Vertex AI quota.",
    )
    gcp_location: str = Field(
        default="global",
        description="Vertex endpoint location. Only 'global' is supported.",
    )
    gemini_model: str = Field(
        ...,
        min_length=1,
        description="Primary extraction model.",
    )
    gemini_model_fallback: str = Field(
        ...,
        min_length=1,
        description="Pro-tier escalation model for hard documents. Not used for quota retries.",
    )
    gemini_model_quota_fallback: str = Field(
        ...,
        min_length=1,
        description=(
            "Flash-tier model used only when the primary model is exhausted "
            "(RESOURCE_EXHAUSTED). Must be same-tier so a quota retry is not a "
            "capability downgrade."
        ),
    )
    extraction_runs: int = Field(
        default=3,
        ge=1,
        le=9,
        description=(
            "Number of extraction runs per document. Per-field agreement across "
            "these runs is the reliability signal, replacing the model's own "
            "confidence score. N=1 is supported for cheap iteration and reports "
            "agreement as null rather than 1.0."
        ),
    )
    max_upload_mb: int = Field(
        default=15,
        gt=0,
        le=100,
        description="Largest accepted upload, in megabytes.",
    )

    @field_validator("gcp_location")
    @classmethod
    def _reject_unsupported_location(cls, value: str) -> str:
        normalised = value.strip().lower()
        if normalised not in SUPPORTED_LOCATIONS:
            raise ValueError(
                f"GCP_LOCATION={value!r} is not supported. Gemini 3.x publisher "
                f"models resolve only on the 'global' endpoint in this project; a "
                f"regional endpoint returns 404, not a fallback. "
                f"Supported: {sorted(SUPPORTED_LOCATIONS)}."
            )
        return normalised

    @field_validator(
        "gemini_model", "gemini_model_fallback", "gemini_model_quota_fallback"
    )
    @classmethod
    def _reject_resource_path(cls, value: str) -> str:
        cleaned = value.strip()
        if "/" in cleaned:
            raise ValueError(
                f"Expected a bare model ID such as 'gemini-3.7-flash', got {value!r}. "
                "Do not include the 'publishers/google/models/' prefix."
            )
        return cleaned

    @property
    def max_upload_bytes(self) -> int:
        """:attr:`max_upload_mb` expressed in bytes."""
        return self.max_upload_mb * 1024 * 1024

    @property
    def runtime_models(self) -> tuple[str, ...]:
        """Every model that may serve a request, deduplicated, in priority order.

        This is what :func:`rxconcile.gcp.models.assert_models_resolve` checks at
        boot: a Preview model ID can disappear, and each of these can serve
        production traffic.
        """
        ordered = (
            self.gemini_model,
            self.gemini_model_quota_fallback,
            self.gemini_model_fallback,
        )
        seen: dict[str, None] = {}
        for model in ordered:
            seen.setdefault(model, None)
        return tuple(seen)


def _load() -> Settings:
    if os.environ.get(_KEYFILE_ENV_VAR):
        raise ConfigError(
            f"{_KEYFILE_ENV_VAR} is set, pointing at a service account key file. "
            "rxconcile authenticates with Application Default Credentials only and "
            "never uses key files. Unset it and run: gcloud auth application-default login"
        )
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        missing = [
            str(err["loc"][0]).upper()
            for err in exc.errors()
            if err["type"] == "missing"
        ]
        hint = (
            f"Missing required key(s): {', '.join(missing)}. "
            if missing
            else ""
        )
        raise ConfigError(
            f"Invalid rxconcile configuration. {hint}"
            f"Expected an env file at {_ENV_FILE} (copy .env.example and fill it in) "
            f"or the equivalent environment variables.\n{exc}"
        ) from exc


#: Process-wide settings. Constructed at import so failures surface at boot.
settings: Final[Settings] = _load()
