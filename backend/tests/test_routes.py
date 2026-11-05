"""Tests for the HTTP routes. See docs/DESIGN.md, section 7."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fakes import OWNER, REDIRECT_DOMAIN, STRANGER, FakeWorld

from handlers.routes import create_app
from models.actor import Actor

DYNAMIC_BODY = {
    "code": "bldc-flyer",
    "kind": "dynamic",
    "destination": "https://example.com/page",
    "campaign": "bldc",
}


class Api:
    """Sends API Gateway events to the real routes."""

    def __init__(self) -> None:
        self.world = FakeWorld()
        self.app = create_app(self.world.deps)

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        actor: Actor = OWNER,
        query: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Send one request and return the status and parsed body."""
        event = {
            "version": "2.0",
            "routeKey": "$default",
            "rawPath": path,
            "rawQueryString": "",
            "headers": {},
            "queryStringParameters": query or {},
            "requestContext": {
                "requestId": "request-1",
                "stage": "$default",
                "http": {"method": method, "path": path},
                "authorizer": {
                    "jwt": {"claims": {"sub": actor.sub, "email": actor.email}, "scopes": []}
                },
            },
            "isBase64Encoded": False,
        }
        if isinstance(body, str):
            event["body"] = body
        elif body is not None:
            event["body"] = json.dumps(body)

        response = self.app.resolve(event, {})
        return response["statusCode"], json.loads(response["body"])


@pytest.fixture
def api() -> Api:
    """Provide a fresh API for each test."""
    return Api()


def test_create_returns_201_and_the_contract_fields(api):
    status, body = api.call("POST", "/codes", DYNAMIC_BODY)

    assert status == 201
    assert body["status"] == "live"
    assert body["encoded_url"] == f"https://{REDIRECT_DOMAIN}/bldc-flyer"
    assert body["allowed_actions"] == ["update", "download"]
    assert "utm_campaign=bldc" in body["final_url"]


def test_internal_fields_never_reach_a_client(api):
    _, body = api.call("POST", "/codes", DYNAMIC_BODY)

    assert not {"owner_sub", "version", "synced", "rendered"} & set(body)


def test_repeating_a_create_returns_200(api):
    api.call("POST", "/codes", DYNAMIC_BODY)

    status, _ = api.call("POST", "/codes", DYNAMIC_BODY)

    assert status == 200


def test_a_taken_name_returns_409(api):
    api.call("POST", "/codes", DYNAMIC_BODY)

    status, body = api.call("POST", "/codes", DYNAMIC_BODY, actor=STRANGER)

    assert status == 409
    assert body["error"]["type"] == "code_taken"


def test_a_broken_rule_returns_field_errors(api):
    status, body = api.call("POST", "/codes", {**DYNAMIC_BODY, "code": "api"})

    assert status == 400
    assert body["error"]["type"] == "validation"
    assert body["error"]["fields"] == [{"field": "code", "message": "Code is reserved"}]


@pytest.mark.parametrize(
    "bad_body",
    ["not json", {"code": "x"}, {**DYNAMIC_BODY, "kind": "other"}, {**DYNAMIC_BODY, "extra": 1}],
)
def test_a_malformed_body_returns_400(api, bad_body):
    status, body = api.call("POST", "/codes", bad_body)

    assert status == 400
    assert body["error"]["fields"]


def test_a_failed_step_returns_502_naming_the_step(api):
    api.world.renderer.fail = True

    status, body = api.call("POST", "/codes", DYNAMIC_BODY)

    assert status == 502
    assert body["error"]["type"] == "render_failed"


def test_list_returns_only_the_callers_codes(api):
    api.call("POST", "/codes", DYNAMIC_BODY)

    _, mine = api.call("GET", "/codes")
    _, theirs = api.call("GET", "/codes", actor=STRANGER)

    assert [code["code"] for code in mine["codes"]] == ["bldc-flyer"]
    assert theirs["codes"] == []


def test_another_users_code_returns_404_on_every_route(api):
    api.call("POST", "/codes", DYNAMIC_BODY)

    assert api.call("GET", "/codes/bldc-flyer", actor=STRANGER)[0] == 404
    assert api.call("PATCH", "/codes/bldc-flyer", {"label": "x"}, actor=STRANGER)[0] == 404
    assert api.call("GET", "/codes/bldc-flyer/image", actor=STRANGER)[0] == 404


def test_patch_updates_a_code(api):
    api.call("POST", "/codes", DYNAMIC_BODY)

    status, body = api.call("PATCH", "/codes/bldc-flyer", {"label": "Flyer"})

    assert status == 200
    assert body["label"] == "Flyer"


def test_patch_with_no_body_republishes(api):
    api.call("POST", "/codes", DYNAMIC_BODY)

    status, _ = api.call("PATCH", "/codes/bldc-flyer")

    assert status == 200
    assert api.world.redirects.calls == 2


def test_kind_cannot_be_changed(api):
    api.call("POST", "/codes", DYNAMIC_BODY)

    status, _ = api.call("PATCH", "/codes/bldc-flyer", {"kind": "static"})

    assert status == 400


def test_image_returns_a_url_and_its_lifetime(api):
    api.call("POST", "/codes", DYNAMIC_BODY)

    status, body = api.call("GET", "/codes/bldc-flyer/image")

    assert status == 200
    assert body["url"].endswith("/bldc-flyer/full.png")
    assert body["expires_in"] == 300


def test_image_uses_the_requested_logo(api):
    api.call("POST", "/codes", DYNAMIC_BODY)

    _, body = api.call("GET", "/codes/bldc-flyer/image", query={"logo": "flower"})

    assert body["url"].endswith("/bldc-flyer/flower.png")


def test_a_pending_code_cannot_be_downloaded(api):
    api.world.renderer.fail = True
    api.call("POST", "/codes", DYNAMIC_BODY)

    status, body = api.call("GET", "/codes/bldc-flyer/image")

    assert status == 409
    assert body["error"]["type"] == "not_ready"


def test_an_unexpected_error_returns_500_without_details(api):
    api.world.repository.get = None

    status, body = api.call("GET", "/codes/bldc-flyer")

    assert status == 500
    assert body["error"]["message"] == "Something went wrong"