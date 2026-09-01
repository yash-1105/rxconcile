"""The configuration a deployment changes, and the local defaults it must not.

Everything here has a working local default. That is the property worth
guarding: `make dev` needs no new variable set, and a developer who has never
heard of Railway sees no change at all. Each test therefore checks both halves
-- what the variable does when set, and what happens when it is absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rxconcile.config import ConfigError, Settings, service_account_info
from rxconcile.gcp.client import _build_client, resolve_credentials
from rxconcile.store.db import DB_PATH, db_path

_CREDS_VAR = "GOOGLE_APPLICATION_CREDENTIALS_JSON"
_KEYFILE_VAR = "GOOGLE_APPLICATION_CREDENTIALS"

#: Structurally complete and cryptographically worthless. Enough to exercise the
#: parsing and the field check; nothing here builds real credentials from it,
#: because google-auth would rightly refuse.
#:
#: Deliberately NOT shaped like a PEM block. A fixture carrying a real-looking
#: PEM header trips every secret scanner that ever looks at this repo --
#: including the history scan CLAUDE.md rule 9 calls for, and GitHub's push
#: protection -- and a scanner that always fires is one nobody reads. The test
#: does not need the shape; it needs the field to be present.
FAKE_KEY: dict[str, str] = {
    "type": "service_account",
    "project_id": "rxconcile-test",
    "private_key_id": "0" * 40,
    "private_key": "not-a-real-key",
    "client_email": "rxconcile@rxconcile-test.iam.gserviceaccount.com",
}


def make(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "gcp_project_id": "proj-1234",
        "gemini_model": "gemini-3.7-flash",
        "gemini_model_fallback": "gemini-3.1-pro-preview",
        "gemini_model_quota_fallback": "gemini-3.6-flash",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestServiceAccountFromTheEnvironment:
    """The deployed credential path. Never a file — see CLAUDE.md rule 9."""

    def test_absent_means_adc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The local path, and the reason this can stay unset everywhere."""
        monkeypatch.delenv(_CREDS_VAR, raising=False)
        assert service_account_info() is None

    def test_blank_means_adc_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A platform that sets every variable, empty ones included.

        An empty string is not an attempt to supply a key, and treating it as
        one would fail a deployment for a variable nobody filled in.
        """
        monkeypatch.setenv(_CREDS_VAR, "   ")
        assert service_account_info() is None

    def test_a_whole_key_is_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_CREDS_VAR, json.dumps(FAKE_KEY))
        info = service_account_info()
        assert info is not None
        assert info["client_email"] == FAKE_KEY["client_email"]

    def test_nothing_is_written_to_disk(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The rule this whole design exists to satisfy.

        Parsing must leave no artefact anywhere — a temp file created to satisfy
        a library that wants a path is exactly the key file rule 9 forbids.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(_CREDS_VAR, json.dumps(FAKE_KEY))
        service_account_info()
        assert list(tmp_path.rglob("*")) == []

    def test_malformed_json_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_CREDS_VAR, "{not json")
        with pytest.raises(ConfigError, match="not valid JSON"):
            service_account_info()

    def test_a_json_string_is_not_a_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid JSON, wrong shape. A double-encoded value lands here."""
        monkeypatch.setenv(_CREDS_VAR, '"just a string"')
        with pytest.raises(ConfigError, match="not an object"):
            service_account_info()

    @pytest.mark.parametrize("field", ["type", "project_id", "private_key", "client_email"])
    def test_a_missing_field_is_named(
        self, monkeypatch: pytest.MonkeyPatch, field: str
    ) -> None:
        """Named individually, because "invalid credentials" sends someone to
        the IAM console when a shell ate the newlines in private_key."""
        partial = {k: v for k, v in FAKE_KEY.items() if k != field}
        monkeypatch.setenv(_CREDS_VAR, json.dumps(partial))
        with pytest.raises(ConfigError, match=field):
            service_account_info()

    def test_the_keyfile_variable_is_still_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rule 9 permits a key in the environment, not a key file.

        GOOGLE_APPLICATION_CREDENTIALS names a path on disk, so it stays
        forbidden. Guarding this is the difference between the amendment and a
        general relaxation.
        """
        monkeypatch.setenv(_KEYFILE_VAR, "/tmp/key.json")
        monkeypatch.delenv(_CREDS_VAR, raising=False)
        with pytest.raises(RuntimeError, match=_KEYFILE_VAR):
            _build_client()

    def test_a_bad_key_fails_loudly_not_silently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A structurally valid key whose private_key is not a key.

        google-auth raises; the point is that it surfaces as a ConfigError
        naming the variable, rather than as an auth error much later.
        """
        monkeypatch.setenv(_CREDS_VAR, json.dumps(FAKE_KEY))
        with pytest.raises(ConfigError, match=_CREDS_VAR):
            resolve_credentials()


class TestAllowedOrigins:
    def test_default_is_the_dev_server(self) -> None:
        assert make().origins == ("http://localhost:5173",)

    def test_comma_separated(self) -> None:
        cfg = make(allowed_origins="https://a.example,https://b.example")
        assert cfg.origins == ("https://a.example", "https://b.example")

    def test_whitespace_and_blanks_are_dropped(self) -> None:
        """A trailing comma must not become an empty origin.

        An empty origin reaches the CORS middleware as a literal "" and matches
        nothing, which reads as a server bug rather than a config typo.
        """
        cfg = make(allowed_origins=" https://a.example , , https://b.example ,")
        assert cfg.origins == ("https://a.example", "https://b.example")

    def test_empty_means_no_origin_not_one_blank_origin(self) -> None:
        assert make(allowed_origins="").origins == ()


class TestDatabasePath:
    def test_unset_is_the_local_default(self) -> None:
        assert make().database_path is None
        assert db_path() == DB_PATH

    def test_set_overrides(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "volume" / "rxconcile.db"
        monkeypatch.setattr("rxconcile.store.db.settings", make(database_path=target))
        assert db_path() == target


class TestCorsAdvertisesEveryMethodTheApiServes:
    """A preflight the browser fails is a dead button, not an error message.

    These routes worked locally only because the dev proxy makes the browser
    same-origin, where no preflight happens at all. Cross-origin — which is the
    entire point of deploying — an unadvertised method is refused before the
    request is ever sent, and nothing reaches the server to be logged.
    """

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/scans"),
            ("POST", "/api/scans"),
            ("PUT", "/api/allowance"),
            ("PATCH", "/api/scans/1/decisions"),
            ("DELETE", "/api/scans/1"),
        ],
    )
    def test_preflight_allows_it(self, method: str, path: str) -> None:
        from fastapi.testclient import TestClient

        from rxconcile.main import app

        with TestClient(app) as client:
            response = client.options(
                path,
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": method,
                },
            )
        assert response.status_code == 200, f"{method} {path} preflight refused"
        allowed = response.headers["access-control-allow-methods"]
        assert method in allowed, f"{method} missing from {allowed!r}"

    def test_an_unknown_origin_is_not_allowed(self) -> None:
        """The default admits the dev server and nothing else."""
        from fastapi.testclient import TestClient

        from rxconcile.main import app

        with TestClient(app) as client:
            response = client.options(
                "/api/scans",
                headers={
                    "Origin": "https://not-configured.example",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert "access-control-allow-origin" not in response.headers


class TestStartupDoesTheWorkBeforeTheFirstRequest:
    def test_the_schema_exists_before_anything_is_served(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A fresh deployed database gets its schema at boot, not on first use.

        This used to happen inside whichever request first opened a session,
        which put the one operation most likely to fail on a new deployment
        behind a user action instead of behind the deploy.
        """
        import sqlite3

        from fastapi.testclient import TestClient

        import rxconcile.store.db as db

        target = tmp_path / "fresh" / "rxconcile.db"
        monkeypatch.setattr(db, "settings", make(database_path=target))
        monkeypatch.setattr(db, "_engine", None)

        from rxconcile.main import app

        assert not target.exists(), "fixture must start with no database"
        with TestClient(app):
            # Inside the lifespan, before a single request has been made.
            assert target.exists(), "startup did not create the database"
            columns = sqlite3.connect(target).execute(
                "select count(*) from pragma_table_info('scan_record')"
            ).fetchone()[0]
            assert columns > 0, "startup created a file but no schema"

        monkeypatch.setattr(db, "_engine", None)
