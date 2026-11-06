"""Tests for the Lambda entry point: pairable logs and correlation ids.

See docs/DESIGN.md, section 9.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from types import ModuleType

import pytest
from fakes import FakeWorld, api_event

from common.logger import logger
from handlers.routes import create_app

ENVIRONMENT = ("CODES_TABLE", "KVS_ARN", "IMAGES_BUCKET", "RENDER_FUNCTION", "REDIRECT_DOMAIN")


@pytest.fixture
def world() -> FakeWorld:
    """Provide a fresh set of fakes for each test."""
    return FakeWorld()


@pytest.fixture
def handler(monkeypatch, world) -> ModuleType:
    """Load the entry point, with fakes in place of the real adapters."""
    for name in ENVIRONMENT:
        monkeypatch.setenv(name, "test")

    module = importlib.import_module("api_handler")
    monkeypatch.setattr(module, "app", create_app(world.deps))
    return module


@pytest.fixture
def logged(monkeypatch) -> list[tuple[int, str]]:
    """Capture each log line as its level and final text."""
    lines: list[tuple[int, str]] = []

    def recorder(level: int) -> Callable[..., None]:
        def record(message: str, *args, **_kwargs) -> None:
            lines.append((level, message % args))

        return record

    def record_at_level(level: int, message: str, *args, **kwargs) -> None:
        recorder(level)(message, *args, **kwargs)

    monkeypatch.setattr(logger, "info", recorder(logging.INFO))
    monkeypatch.setattr(logger, "warning", recorder(logging.WARNING))
    monkeypatch.setattr(logger, "error", recorder(logging.ERROR))
    monkeypatch.setattr(logger, "exception", recorder(logging.ERROR))
    monkeypatch.setattr(logger, "log", record_at_level)
    return lines


def test_a_request_logs_a_start_and_an_end(handler, logged):
    handler.lambda_handler(api_event("GET", "/codes"), {})

    assert logged[0][1] == "Request start method=GET path=/codes user=owner-sub"
    assert logged[-1][1].startswith("Request end method=GET path=/codes status=200 duration_ms=")


def test_the_clients_correlation_id_is_used(handler):
    event = api_event("GET", "/codes", headers={"x-correlation-id": "from-client"})

    handler.lambda_handler(event, {})

    assert logger.get_correlation_id() == "from-client"


def test_a_long_correlation_id_is_cut_short(handler):
    event = api_event("GET", "/codes", headers={"x-correlation-id": "a" * 500})

    handler.lambda_handler(event, {})

    assert logger.get_correlation_id() == "a" * handler.MAX_CORRELATION_ID_LENGTH


def test_the_request_id_is_used_when_the_client_sends_none(handler):
    handler.lambda_handler(api_event("GET", "/codes"), {})

    assert logger.get_correlation_id() == "request-1"


def test_a_success_ends_at_info(handler, logged):
    handler.lambda_handler(api_event("GET", "/codes"), {})

    assert logged[-1][0] == logging.INFO


def test_a_client_error_ends_at_warning(handler, logged):
    handler.lambda_handler(api_event("GET", "/codes/missing"), {})

    assert logged[-1][0] == logging.WARNING
    assert "status=404" in logged[-1][1]


def test_a_server_error_ends_at_error(handler, logged, world):
    world.repository.get = None

    handler.lambda_handler(api_event("GET", "/codes/any"), {})

    assert logged[-1][0] == logging.ERROR
    assert "status=500" in logged[-1][1]


def test_a_failure_outside_the_routes_is_logged_and_raised(handler, logged, monkeypatch):
    def fail(_event, _context):
        raise RuntimeError("resolver failed")

    monkeypatch.setattr(handler.app, "resolve", fail)

    with pytest.raises(RuntimeError):
        handler.lambda_handler(api_event("GET", "/codes"), {})

    assert logged[-1][0] == logging.ERROR
    assert "status=unhandled" in logged[-1][1]


def test_the_token_is_never_logged(handler, logged):
    event = api_event("GET", "/codes", headers={"authorization": "Bearer secret-token"})

    handler.lambda_handler(event, {})

    assert not [text for _level, text in logged if "secret-token" in text]