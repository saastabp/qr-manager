"""API Lambda entry point.

Builds the app once per container, then wraps each request in pairable entry and exit
logs. The raw event is never logged, because it carries the JWT. See docs/DESIGN.md,
section 9.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from bootstrap import build_dependencies
from common.logger import elapsed_ms, logger
from handlers.routes import create_app

#: Longest client-supplied correlation id kept. The value is untrusted input.
MAX_CORRELATION_ID_LENGTH = 64

app = create_app(build_dependencies())


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle one API Gateway HTTP API request.

    Parameters
    ----------
    event : dict[str, Any]
        The API Gateway HTTP API v2 event.
    context : Any
        The Lambda invocation context.

    Returns
    -------
    dict[str, Any]
        The proxy response.
    """
    start = time.monotonic()
    request_context = event.get("requestContext", {})
    method = request_context.get("http", {}).get("method")
    path = event.get("rawPath")
    claims = request_context.get("authorizer", {}).get("jwt", {}).get("claims", {})

    logger.set_correlation_id(_correlation_id(event))
    logger.info("Request start method=%s path=%s user=%s", method, path, claims.get("sub"))

    try:
        response = app.resolve(event, context)
    except Exception:
        logger.exception(
            "Request end method=%s path=%s status=unhandled duration_ms=%s",
            method,
            path,
            elapsed_ms(start),
        )
        raise

    status = response.get("statusCode", 0)
    logger.log(
        _level_for(status),
        "Request end method=%s path=%s status=%s duration_ms=%s",
        method,
        path,
        status,
        elapsed_ms(start),
    )
    return response


def _correlation_id(event: dict[str, Any]) -> str | None:
    """Use the client's correlation id, or API Gateway's request id if there is none."""
    headers = event.get("headers") or {}
    supplied = headers.get("x-correlation-id")
    if supplied:
        return supplied[:MAX_CORRELATION_ID_LENGTH]

    return event.get("requestContext", {}).get("requestId")


def _level_for(status: int) -> int:
    """Match the exit log's severity to the outcome."""
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING

    return logging.INFO