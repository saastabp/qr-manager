"""API Lambda entry point.

Builds the app once per container, then wraps each request in pairable entry and exit
logs. The raw event is never logged, because it carries the JWT. See docs/DESIGN.md,
section 9.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2

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

    Raises
    ------
    Exception
        Anything the resolver itself could not handle, once it has been logged.
    """
    start = time.monotonic()
    request = APIGatewayProxyEventV2(event)
    method = request.request_context.http.method
    path = request.raw_path
    user = request.request_context.authorizer.jwt_claim.get("sub")

    logger.set_correlation_id(_correlation_id(request))
    logger.info("Request start method=%s path=%s user=%s", method, path, user)

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


def _correlation_id(request: APIGatewayProxyEventV2) -> str:
    """Use the client's correlation id, or API Gateway's request id if there is none."""
    supplied = request.headers.get("x-correlation-id")
    if supplied:
        return supplied[:MAX_CORRELATION_ID_LENGTH]

    return request.request_context.request_id


def _level_for(status: int) -> int:
    """Match the exit log's severity to the outcome."""
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING

    return logging.INFO