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
from cases.completion import DEFAULT_LOGO, Dependencies
from cases.create_code import CreateCodeRequest, create_code
from cases.errors import (
    CodeConflict,
    CodeNotFound,
    CodeNotReady,
    CodeTaken,
    FieldError,
    InvalidInput,
    StepFailed,
)
from cases.get_image import DOWNLOAD_URL_SECONDS, get_image
from cases.view_codes import get_code, list_codes, view_of
from common.logger import logger
from handlers.schemas import ChangeCodeBody, CreateCodeBody, code_response
from models.actor import Actor


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
        """Create a code: 201 if new, 200 if this repeats an earlier request."""
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
        """List the caller's codes."""
        views = list_codes(actor(), deps)
        return _json(200, {"codes": [code_response(view) for view in views]})

    @app.get("/codes/<code>")
    def get_one_code(code: str) -> Response:
        """Read one of the caller's codes."""
        return _json(200, code_response(get_code(code, actor(), deps)))

    @app.patch("/codes/<code>")
    def patch_code(code: str) -> Response:
        """Change a code, or finish and republish it if the body is empty."""
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
        """Return a short-lived download link for a code's PNG."""
        logo = app.current_event.get_query_string_value("logo", DEFAULT_LOGO)
        url = get_image(code, logo, actor(), deps)
        return _json(200, {"url": url, "expires_in": DOWNLOAD_URL_SECONDS})

    @app.exception_handler(InvalidInput)
    def on_invalid(error: InvalidInput) -> Response:
        """Report a broken rule, with one entry per field."""
        fields = [{"field": e.field, "message": e.message} for e in error.errors]
        return _error_response(error, 400, "validation", str(error), fields)

    @app.exception_handler(CodeNotFound)
    def on_not_found(error: CodeNotFound) -> Response:
        """Report a code that is missing or belongs to someone else."""
        return _error_response(error, 404, "not_found", "Code not found")

    @app.exception_handler(CodeTaken)
    def on_taken(error: CodeTaken) -> Response:
        """Report a name that is already in use."""
        return _error_response(error, 409, "code_taken", "That code name is taken")

    @app.exception_handler(CodeNotReady)
    def on_not_ready(error: CodeNotReady) -> Response:
        """Report a code that is still pending."""
        return _error_response(error, 409, "not_ready", "The code is not ready yet")

    @app.exception_handler(CodeConflict)
    def on_conflict(error: CodeConflict) -> Response:
        """Report a change that lost to a newer one, and was not saved."""
        return _error_response(error, 409, "conflict", "The code just changed. Try again")

    @app.exception_handler(StepFailed)
    def on_step_failed(error: StepFailed) -> Response:
        """Report a publish or render that failed, naming the step."""
        return _error_response(error, 502, f"{error.step}_failed", str(error))

    @app.exception_handler(Exception)
    def on_unexpected(error: Exception) -> Response:
        """Report anything else, without revealing details."""
        return _error_response(error, 500, "internal", "Something went wrong")

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
    error: Exception,
    status: int,
    kind: str,
    message: str,
    fields: list[dict[str, str]] | None = None,
) -> Response:
    """Build an error response, and log its body and stack trace."""
    content = {
        "error": {"type": kind, "message": message, "fields": fields or []},
        "correlation_id": logger.get_correlation_id(),
    }
    logger.error("Error response status=%s body=%s", status, json.dumps(content), exc_info=error)

    return _json(status, content)