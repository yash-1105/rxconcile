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

import json
import os
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_ENV_FILE: Final[Path] = _REPO_ROOT / ".env"

#: The directory the installed package sits in -- ``api/`` in a checkout, and
#: the container root in a deployment, because Railway's root directory is
#: ``api/`` and its contents become the image root. Anything the app must be
#: able to READ at runtime has to live under here or it is simply not shipped.
_PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: Vertex resolves Gemini 3.x publisher models only on the ``global`` endpoint.
#: ``us-central1`` returns 404 for ``gemini-3.7-flash`` in this project, so a
#: regional value is a configuration error, not a fallback.
SUPPORTED_LOCATIONS: Final[frozenset[str]] = frozenset({"global"})

#: Setting this points google-auth at a service account key file ON DISK, which
#: hard rule 9 forbids outright. Rejected rather than honoured -- see
#: ``_CREDENTIALS_ENV_VAR`` for the supported way to supply a service account.
_KEYFILE_ENV_VAR: Final[str] = "GOOGLE_APPLICATION_CREDENTIALS"

#: The service account key itself, as JSON, in the environment. This is the
#: deployed path: the material never touches the filesystem, so there is no
#: file to commit and none to leak. Absent locally, where ADC is used instead.
_CREDENTIALS_ENV_VAR: Final[str] = "GOOGLE_APPLICATION_CREDENTIALS_JSON"

#: Fields without which a key is unusable. Checked so a truncated or
#: shell-mangled value is reported as such, rather than as an auth failure
#: hours later.
_REQUIRED_KEY_FIELDS: Final[tuple[str, ...]] = (
    "type", "project_id", "private_key", "client_email",
)


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

    #: How to read a numeric date whose day and month are BOTH 12 or under.
    #:
    #: ``12-08-2026`` is 12 August under ``dmy`` and 8 December under ``mdy``,
    #: and the document itself does not say which. Indian prescriptions and
    #: pharmacy bills are written day-first, so that is the default here.
    #:
    #: ``strict`` refuses such a date outright, which was the only behaviour
    #: before this setting existed. It is the right choice for a corpus of
    #: mixed origin, where a wrong date is worse than a missing one.
    #:
    #: **An assumed date is never presented as a read one.** The document
    #: records that the order was assumed, and the engine raises
    #: DATE_ORDER_ASSUMED so a reviewer sees the interpretation they are
    #: relying on.
    date_order: Literal["dmy", "mdy", "strict"] = "dmy"

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
    database_path: Path | None = Field(
        default=None,
        description=(
            "Where the SQLite file lives. None means the local default beside "
            "the repo. Deployments set this to a mounted volume, because the "
            "default is derived from the package location and a container "
            "installs the package somewhere else entirely."
        ),
    )
    samples_path: Path | None = Field(
        default=None,
        description=(
            "Where the bundled demo documents live. None means the copy shipped "
            "beside the package, which is the right answer both in a checkout "
            "and in a container."
        ),
    )
    allowed_origins: str = Field(
        default="http://localhost:5173",
        description=(
            "Comma-separated browser origins allowed to call this API. The "
            "default is the Vite dev server; a deployment sets its web origin."
        ),
    )

    @property
    def origins(self) -> tuple[str, ...]:
        """`allowed_origins`, parsed. See :func:`_origins_of`."""
        return _origins_of(self.allowed_origins)

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


def samples_dir() -> Path:
    """The bundled demo documents: `SAMPLES_PATH` if set, else beside the package.

    These moved under ``api/`` to be here at all. They used to sit at the repo
    root, which is outside the deployed build context, so every sample route
    would have 404'd in the container while working perfectly on a laptop.
    """
    return settings.samples_path or _PACKAGE_ROOT / "samples"


def _origins_of(raw: str) -> tuple[str, ...]:
    """Browser origins from a comma-separated string.

    Blanks are dropped so a trailing comma, or the empty string, yields no
    origin rather than an empty one -- an empty origin would be sent to the
    CORS middleware as a literal "" and match nothing, which looks like a
    server bug rather than a configuration typo.
    """
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def service_account_info() -> dict[str, Any] | None:
    """The service account key carried in an environment variable, or None.

    None means the variable is unset, which is the LOCAL path: authenticate
    with Application Default Credentials as this project always has.

    Parsed here and never written anywhere. Hard rule 9 forbids a key FILE, and
    a temp file created to satisfy a library that wants a path is exactly the
    file it forbids -- so this returns the parsed material and the caller hands
    it to an API that accepts credentials directly.
    """
    raw = os.environ.get(_CREDENTIALS_ENV_VAR, "").strip()
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{_CREDENTIALS_ENV_VAR} is set but is not valid JSON. It must hold the "
            f"whole service account key, including its braces. ({exc})"
        ) from exc
    if not isinstance(info, dict):
        raise ConfigError(
            f"{_CREDENTIALS_ENV_VAR} parsed to {type(info).__name__}, not an object. "
            "It must hold the whole service account key."
        )
    missing = [key for key in _REQUIRED_KEY_FIELDS if not info.get(key)]
    if missing:
        # Named individually: "invalid credentials" sends someone to the IAM
        # console when the real problem is a shell that ate the newlines in
        # private_key.
        raise ConfigError(
            f"{_CREDENTIALS_ENV_VAR} is missing required field(s): "
            f"{', '.join(missing)}. Supply the key file's full JSON, unmodified."
        )
    return info


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
