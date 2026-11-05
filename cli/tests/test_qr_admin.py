"""Tests for the qr-admin client. Nothing here reaches AWS."""

from __future__ import annotations

import json
import logging
import stat
import time
from types import SimpleNamespace

import pytest
import requests
from botocore.exceptions import ClientError

import qr_admin
from qr_admin import ApiClient, CliError, Session, Settings, load_settings

SETTINGS = Settings(env="sandbox", api_url="https://api.example.com", client_id="client")
OUTPUTS = {
    "qr-manager-sandbox-Api": {"HttpApiUrlCCB963E2": "https://api.example.com"},
    "qr-manager-sandbox-Auth": {"CliClientId": "client", "UserPoolId": "pool"},
}
CODE = {
    "code": "bldc-flyer",
    "kind": "dynamic",
    "status": "live",
    "encoded_url": "https://qr.example.com/bldc-flyer",
    "destination": "https://example.com",
    "final_url": "https://example.com",
    "label": "",
    "campaign": "",
}


class FakeCognito:
    """Returns a chosen response, and records each sign-in."""

    def __init__(self, response=None, error=None) -> None:
        self.response = response or {
            "AuthenticationResult": {
                "IdToken": "id-token",
                "RefreshToken": "refresh-token",
                "ExpiresIn": 3600,
            }
        }
        self.error = error
        self.flows: list[str] = []

    def initiate_auth(self, **kwargs) -> dict:
        """Record the flow, then answer."""
        self.flows.append(kwargs["AuthFlow"])
        if self.error:
            raise self.error

        return self.response


class FakeResponse:
    """A minimal requests response."""

    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self.ok = status < 400
        self.body = body

    def json(self) -> dict:
        """Return the body, or fail the way requests does for non-JSON."""
        if self.body is None:
            raise ValueError("not JSON")

        return self.body


class FakeSession:
    """Supplies a fixed token."""

    def id_token(self) -> str:
        """Return the token."""
        return "id-token"


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    """Capture API requests, answering each with the code above."""
    calls: list[dict] = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse(200, {**CODE, "codes": [CODE]})

    monkeypatch.setattr(qr_admin.requests, "request", fake_request)
    return calls


def test_settings_are_read_from_the_outputs_file(tmp_path):
    (tmp_path / "outputs-sandbox.json").write_text(json.dumps(OUTPUTS))

    assert load_settings("sandbox", tmp_path) == SETTINGS


def test_a_missing_outputs_file_says_how_to_create_it(tmp_path):
    with pytest.raises(CliError, match="outputs-sandbox.json"):
        load_settings("sandbox", tmp_path)


def test_incomplete_outputs_are_refused(tmp_path):
    (tmp_path / "outputs-sandbox.json").write_text("{}")

    with pytest.raises(CliError, match="missing"):
        load_settings("sandbox", tmp_path)


def test_login_caches_tokens_privately_without_the_password(tmp_path):
    Session(SETTINGS, tmp_path, FakeCognito()).login("user@example.com", "secret-password")

    path = tmp_path / "tokens-sandbox.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "secret-password" not in path.read_text()


def test_a_current_token_is_reused(tmp_path):
    cognito = FakeCognito()
    session = Session(SETTINGS, tmp_path, cognito)
    session.login("user@example.com", "password")

    assert session.id_token() == "id-token"
    assert cognito.flows == ["USER_PASSWORD_AUTH"]


def test_an_expired_token_is_refreshed_and_keeps_the_refresh_token(tmp_path):
    path = tmp_path / "tokens-sandbox.json"
    path.write_text(
        json.dumps({"id_token": "old", "refresh_token": "keep", "expires_at": time.time() - 1})
    )
    response = {"AuthenticationResult": {"IdToken": "new", "ExpiresIn": 3600}}
    cognito = FakeCognito(response)

    token = Session(SETTINGS, tmp_path, cognito).id_token()

    assert token == "new"
    assert cognito.flows == ["REFRESH_TOKEN_AUTH"]
    assert json.loads(path.read_text())["refresh_token"] == "keep"


def test_no_cached_tokens_says_to_sign_in(tmp_path):
    with pytest.raises(CliError, match="login"):
        Session(SETTINGS, tmp_path, FakeCognito()).id_token()


def test_a_refused_sign_in_is_a_clean_error(tmp_path):
    error = ClientError({"Error": {"Code": "NotAuthorizedException", "Message": "no"}}, "Auth")

    with pytest.raises(CliError, match="Sign-in failed"):
        Session(SETTINGS, tmp_path, FakeCognito(error=error)).login("a@b.c", "wrong")


def test_an_mfa_challenge_is_a_clean_error(tmp_path):
    cognito = FakeCognito({"ChallengeName": "SOFTWARE_TOKEN_MFA"})

    with pytest.raises(CliError, match="SOFTWARE_TOKEN_MFA"):
        Session(SETTINGS, tmp_path, cognito).login("a@b.c", "password")


def test_requests_carry_the_token_and_a_correlation_id(sent):
    ApiClient(SETTINGS, FakeSession()).call("GET", "/codes")

    assert sent[0]["url"] == "https://api.example.com/codes"
    assert sent[0]["headers"]["authorization"] == "Bearer id-token"
    assert sent[0]["headers"]["x-correlation-id"]


def test_server_errors_show_the_message_fields_and_correlation_id(monkeypatch):
    body = {
        "error": {
            "message": "Code is reserved",
            "fields": [{"field": "code", "message": "Code is reserved"}],
        }
    }
    monkeypatch.setattr(qr_admin.requests, "request", lambda *a, **k: FakeResponse(400, body))

    with pytest.raises(CliError) as caught:
        ApiClient(SETTINGS, FakeSession()).call("POST", "/codes", {})

    assert "code: Code is reserved" in str(caught.value)
    assert "correlation id:" in str(caught.value)


def test_a_non_json_error_still_shows_the_status(monkeypatch):
    monkeypatch.setattr(qr_admin.requests, "request", lambda *a, **k: FakeResponse(401))

    with pytest.raises(CliError, match="401"):
        ApiClient(SETTINGS, FakeSession()).call("GET", "/codes")


def test_a_network_failure_is_a_clean_error(monkeypatch):
    def fail(*_args, **_kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(qr_admin.requests, "request", fail)

    with pytest.raises(CliError, match="Could not reach"):
        ApiClient(SETTINGS, FakeSession()).call("GET", "/codes")


def test_update_sends_only_the_options_given(sent):
    api = ApiClient(SETTINGS, FakeSession())
    args = SimpleNamespace(code="bldc-flyer", dest=None, label="New", campaign="")

    qr_admin._run_update(args, FakeSession(), api)

    assert sent[0]["json"] == {"label": "New", "campaign": ""}


def test_resync_touches_only_pending_codes(monkeypatch, capsys):
    pending = {**CODE, "code": "stuck", "status": "pending"}
    patched: list[str] = []

    def fake_request(method, url, **_kwargs):
        if method == "PATCH":
            patched.append(url)
            return FakeResponse(200, {**pending, "status": "live"})

        return FakeResponse(200, {"codes": [CODE, pending]})

    monkeypatch.setattr(qr_admin.requests, "request", fake_request)
    api = ApiClient(SETTINGS, FakeSession())

    qr_admin._run_resync(SimpleNamespace(all=False), FakeSession(), api)

    assert patched == ["https://api.example.com/codes/stuck"]
    assert "Resynced 1 of 2" in capsys.readouterr().out


def test_the_environment_must_be_chosen():
    with pytest.raises(SystemExit):
        qr_admin.build_parser().parse_args(["list"])


def test_errors_exit_with_status_1(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(qr_admin, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(qr_admin.load_settings, "__defaults__", (tmp_path,))

    assert qr_admin.main(["--env", "sandbox", "list"]) == 1
    assert "Error:" in capsys.readouterr().err


def test_verbose_keeps_aws_debug_logging_off(monkeypatch, tmp_path):
    """Debug on the root logger would print the password botocore sends at sign-in."""
    root_levels: list[int] = []
    monkeypatch.setattr(
        qr_admin.logging, "basicConfig", lambda **kwargs: root_levels.append(kwargs["level"])
    )
    monkeypatch.setattr(qr_admin.load_settings, "__defaults__", (tmp_path,))
    monkeypatch.setattr(qr_admin, "logger", logging.getLogger("qr-admin-test"))

    qr_admin.main(["--env", "sandbox", "--verbose", "list"])

    assert root_levels == [logging.WARNING]
    assert qr_admin.logger.level == logging.DEBUG