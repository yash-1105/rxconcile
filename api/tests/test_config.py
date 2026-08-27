"""Configuration validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rxconcile.config import Settings, settings


def make(**overrides: object) -> Settings:
    # All three model keys are required: config.py deliberately has no
    # Python-side model defaults (CLAUDE.md hard rule 6).
    base: dict[str, object] = {
        "gcp_project_id": "proj-1234",
        "gemini_model": "gemini-3.7-flash",
        "gemini_model_fallback": "gemini-3.1-pro-preview",
        "gemini_model_quota_fallback": "gemini-3.6-flash",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_env_file_loads_real_values() -> None:
    assert settings.gcp_project_id
    assert settings.gemini_model
    assert settings.gcp_location == "global"


def test_defaults_applied() -> None:
    cfg = make()
    assert cfg.gcp_location == "global"
    assert cfg.max_upload_mb == 15
    assert cfg.max_upload_bytes == 15 * 1024 * 1024


@pytest.mark.parametrize(
    "key",
    ["gemini_model", "gemini_model_fallback", "gemini_model_quota_fallback"],
)
def test_every_runtime_model_is_required(key: str) -> None:
    """No model ID may default from Python; .env is the only source."""
    base = {
        "gcp_project_id": "proj-1234",
        "gemini_model": "gemini-3.7-flash",
        "gemini_model_fallback": "gemini-3.1-pro-preview",
        "gemini_model_quota_fallback": "gemini-3.6-flash",
    }
    del base[key]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **base)  # type: ignore[call-arg,arg-type]


def test_regional_location_rejected() -> None:
    """us-central1 404s for Gemini 3.x here, so it must not be accepted."""
    with pytest.raises(ValidationError, match="only on the 'global' endpoint"):
        make(gcp_location="us-central1")


def test_location_is_normalised() -> None:
    assert make(gcp_location="  GLOBAL ").gcp_location == "global"


def test_blank_project_id_rejected() -> None:
    with pytest.raises(ValidationError):
        make(gcp_project_id="")


def test_full_resource_path_rejected() -> None:
    with pytest.raises(ValidationError, match="bare model ID"):
        make(gemini_model="publishers/google/models/gemini-3.7-flash")


def test_upload_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        make(max_upload_mb=0)
    with pytest.raises(ValidationError):
        make(max_upload_mb=101)


def test_settings_are_frozen() -> None:
    with pytest.raises(ValidationError):
        make().gcp_project_id = "other"  # type: ignore[misc]


def test_runtime_models_ordered_and_deduplicated() -> None:
    cfg = make(
        gemini_model="gemini-3.7-flash",
        gemini_model_quota_fallback="gemini-3.6-flash",
        gemini_model_fallback="gemini-3.7-flash",
    )
    assert cfg.runtime_models == ("gemini-3.7-flash", "gemini-3.6-flash")
