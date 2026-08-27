"""Retry and model-fallback behaviour, exercised without touching the network."""

from __future__ import annotations

import pytest
from google.genai import errors as genai_errors

from rxconcile.config import settings
from rxconcile.gcp import health, retry
from rxconcile.gcp.errors import VertexUnavailableError


def api_error(code: int) -> genai_errors.APIError:
    """Build an APIError carrying ``code``, as the SDK would raise."""
    return genai_errors.APIError(code, {"error": {"message": f"synthetic {code}"}})


class FakeResponse:
    def __init__(self, text: str = "OK") -> None:
        self.text = text
        self.usage_metadata = None


#: A scripted outcome: either a response to return or an error to raise.
Outcome = FakeResponse | BaseException
Script = dict[str, list[Outcome]]


class FakeModels:
    """Replays a scripted sequence of outcomes per model."""

    def __init__(self, script: Script) -> None:
        self.script: Script = {k: list(v) for k, v in script.items()}
        self.calls: list[str] = []

    def generate_content(
        self, *, model: str, contents: object, config: object
    ) -> FakeResponse:
        self.calls.append(model)
        queue = self.script.get(model)
        if not queue:
            raise AssertionError(f"unexpected call to {model!r}")
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, script: Script) -> None:
        self.models = FakeModels(script)


@pytest.fixture(autouse=True)
def _no_sleep_and_clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry, "_sleep", lambda _seconds: None)
    health.reset()


def install(monkeypatch: pytest.MonkeyPatch, script: Script) -> FakeClient:
    client = FakeClient(script)
    monkeypatch.setattr(retry, "get_client", lambda: client)
    return client


PRIMARY = settings.gemini_model
QUOTA_FALLBACK = settings.gemini_model_quota_fallback


def test_success_on_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch, {PRIMARY: [FakeResponse()]})
    result = retry.generate_content("hi")
    assert result.model == PRIMARY
    assert result.attempts == 1
    assert result.used_quota_fallback is False
    assert client.models.calls == [PRIMARY]


def test_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(
        monkeypatch, {PRIMARY: [api_error(503), api_error(503), FakeResponse()]}
    )
    result = retry.generate_content("hi")
    assert result.model == PRIMARY
    assert result.attempts == 3
    assert client.models.calls == [PRIMARY] * 3


def test_three_attempts_is_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """503 exhausts three attempts and does NOT switch model."""
    client = install(monkeypatch, {PRIMARY: [api_error(503)] * 3})
    with pytest.raises(VertexUnavailableError, match="not quota exhaustion"):
        retry.generate_content("hi")
    assert client.models.calls == [PRIMARY] * 3
    assert QUOTA_FALLBACK not in client.models.calls


def test_quota_exhaustion_falls_back_to_flash(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(
        monkeypatch,
        {PRIMARY: [api_error(429)] * 3, QUOTA_FALLBACK: [FakeResponse("OK")]},
    )
    result = retry.generate_content("hi")
    assert result.model == QUOTA_FALLBACK
    assert result.used_quota_fallback is True
    assert result.attempts == 4
    assert client.models.calls == [PRIMARY] * 3 + [QUOTA_FALLBACK]


def test_fallback_is_tried_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(
        monkeypatch,
        {PRIMARY: [api_error(429)] * 3, QUOTA_FALLBACK: [api_error(429)]},
    )
    with pytest.raises(VertexUnavailableError, match="out of Vertex quota"):
        retry.generate_content("hi")
    assert client.models.calls.count(QUOTA_FALLBACK) == 1


def test_non_retryable_fails_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 is not retried and not fallen back from."""
    client = install(monkeypatch, {PRIMARY: [api_error(403)]})
    with pytest.raises(genai_errors.APIError):
        retry.generate_content("hi")
    assert client.models.calls == [PRIMARY]


def test_health_records_serving_model(monkeypatch: pytest.MonkeyPatch) -> None:
    install(
        monkeypatch,
        {PRIMARY: [api_error(429)] * 3, QUOTA_FALLBACK: [FakeResponse()]},
    )
    retry.generate_content("hi")
    snapshot = health.health_snapshot()
    assert snapshot.last_served_model == QUOTA_FALLBACK
    assert snapshot.quota_fallback_count == 1
    assert snapshot.request_count == 1


def test_backoff_is_exponential_and_jittered() -> None:
    for attempt, (low, high) in enumerate([(1, 1.25), (2, 2.5), (4, 5)], start=1):
        delay = retry._backoff_seconds(attempt)
        assert low <= delay <= high, f"attempt {attempt} delay {delay}"


def test_backoff_is_capped() -> None:
    assert retry._backoff_seconds(50) <= retry._BACKOFF_MAX_SECONDS * (
        1 + retry._JITTER_RATIO
    )
