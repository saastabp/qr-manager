"""HTTP routes for the API.

Reads requests, calls cases, and turns results and errors into responses. No business
rules live here. See docs/DESIGN.md, section 7.
"""

from __future__ import annotations

import json
from typing import Any

from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response
from pydantic import BaseModel, ValidationError

from cases.change_code import ChangeCodeRequest, change_code
from cases.completion import Dependencies
from cases.create_code import CreateCodeRequest, create_code
from cases.errors import (
    CodeNotFound,
    CodeNotReady,
    CodeTaken,
    FieldError,
    InvalidInput,
    StepFailed,
)
from cases.get_image import get_image
from cases.view_codes import get_code, list_codes, view_of
from common.logger import logger
from handlers.schemas import ChangeCodeBody, CreateCodeBody, code_response
from models.actor import Actor

#: How long an image download link works, in seconds. Reported to clients.
DOWNLOAD_URL_SECONDS = 300


def create_app(deps: Dependencies) -> APIGatewayHttpResolver:
    """Build the API with its routes and error handling.

    Parameters
    ----------
    deps : Dependencies
        The ports and settings for the cases.

    Returns
    -------
    APIGatewayHttpResolver
        The configured resolver.
    """
    app = APIGatewayHttpResolver()

    def actor() -> Actor:
        """Build the caller from the token API Gateway has already verified."""
        claims = app.current_event.request_context.authorizer.jwt_claim
        return Actor(sub=claims["sub"], email=claims["email"])

    def body(schema: type[BaseModel]) -> Any:
        """Parse the request body, turning any problem into field errors."""
        try:
            # API Gateway base64-encodes some bodies, depending on the content type.
            raw = app.current_event.decoded_body or "{}"
            return schema.model_validate(json.loads(raw))
        except ValidationError as error:
            raise InvalidInput(_field_errors(error)) from None
        except ValueError:
            raise InvalidInput([FieldError("body", "Body must be valid JSON")]) from None

    @app.post("/codes")
    def post_code() -> Response:
        request: CreateCodeBody = body(CreateCodeBody)
        code, created = create_code(
            CreateCodeRequest(
                code=request.code,
                kind=request.kind,
                destination=request.destination,
                label=request.label,
                campaign=request.campaign,
            ),
            actor(),
            deps,
        )
        status = 200
        if created:
            status = 201
        return _json(status, code_response(view_of(code, deps)))

    @app.get("/codes")
    def get_codes() -> Response:
        views = list_codes(actor(), deps)
        return _json(200, {"codes": [code_response(view) for view in views]})

    @app.get("/codes/<code>")
    def get_one_code(code: str) -> Response:
        return _json(200, code_response(get_code(code, actor(), deps)))

    @app.patch("/codes/<code>")
    def patch_code(code: str) -> Response:
        request: ChangeCodeBody = body(ChangeCodeBody)
        changed = change_code(
            ChangeCodeRequest(
                code=code,
                destination=request.destination,
                label=request.label,
                campaign=request.campaign,
            ),
            actor(),
            deps,
        )
        return _json(200, code_response(view_of(changed, deps)))

    @app.get("/codes/<code>/image")
    def get_code_image(code: str) -> Response:
        logo = app.current_event.get_query_string_value("logo", "full")
        url = get_image(code, logo, actor(), deps)
        return _json(200, {"url": url, "expires_in": DOWNLOAD_URL_SECONDS})

    @app.exception_handler(InvalidInput)
    def on_invalid(error: InvalidInput) -> Response:
        fields = [{"field": e.field, "message": e.message} for e in error.errors]
        return _error_response(400, "validation", str(error), fields)

    @app.exception_handler(CodeNotFound)
    def on_not_found(_error: CodeNotFound) -> Response:
        return _error_response(404, "not_found", "Code not found")

    @app.exception_handler(CodeTaken)
    def on_taken(_error: CodeTaken) -> Response:
        return _error_response(409, "code_taken", "That code name is taken")

    @app.exception_handler(CodeNotReady)
    def on_not_ready(_error: CodeNotReady) -> Response:
        return _error_response(409, "not_ready", "The code is not ready yet")

    @app.exception_handler(StepFailed)
    def on_step_failed(error: StepFailed) -> Response:
        return _error_response(502, f"{error.step}_failed", str(error))

    @app.exception_handler(Exception)
    def on_unexpected(_error: Exception) -> Response:
        logger.exception("Unexpected error")
        return _error_response(500, "internal", "Something went wrong")

    return app


def _field_errors(error: ValidationError) -> list[FieldError]:
    """Convert pydantic errors to field errors."""
    return [
        FieldError(".".join(str(part) for part in item["loc"]) or "body", item["msg"])
        for item in error.errors()
    ]


def _json(status: int, content: dict[str, Any]) -> Response:
    """Build a JSON response that carries the correlation id."""
    return Response(
        status_code=status,
        content_type="application/json",
        body=json.dumps(content),
        headers={"x-correlation-id": logger.get_correlation_id() or ""},
    )


def _error_response(
    status: int, kind: str, message: str, fields: list[dict[str, str]] | None = None
) -> Response:
    """Build an error response, and log the body for server errors."""
    content = {
        "error": {"type": kind, "message": message, "fields": fields or []},
        "correlation_id": logger.get_correlation_id(),
    }
    if status >= 500:
        logger.error("Error response status=%s body=%s", status, json.dumps(content))

    return _json(status, content)