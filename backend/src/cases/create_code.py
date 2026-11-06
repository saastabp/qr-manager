"""BC1: create a static or dynamic code. See docs/DESIGN.md, section 3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cases import policies
from cases.completion import Dependencies, complete
from cases.errors import CodeTaken
from common.logger import logger
from models.actor import Actor
from models.qr_code import CodeKind, QrCode


@dataclass(frozen=True)
class CreateCodeRequest:
    """What a caller provides to create a code.

    Parameters
    ----------
    code : str
        The requested name.
    kind : CodeKind
        Static or dynamic.
    destination : str
        Where the code should lead.
    label : str
        Optional description.
    campaign : str
        Optional campaign name. Blank means no UTM parameters.
    """

    code: str
    kind: CodeKind
    destination: str
    label: str = ""
    campaign: str = ""


def create_code(
    request: CreateCodeRequest, actor: Actor, deps: Dependencies
) -> tuple[QrCode, bool]:
    """Register a new code and finish it.

    Parameters
    ----------
    request : CreateCodeRequest
        What the caller provided.
    actor : Actor
        The caller, who becomes the owner.
    deps : Dependencies
        The ports and settings.

    Returns
    -------
    tuple[QrCode, bool]
        The finished code, and True if it is new or False if this was a retry.

    Raises
    ------
    InvalidInput
        If the request breaks a business rule.
    CodeTaken
        If the name exists and this is not a repeat of the same request.
    StepFailed
        If publishing or rendering fails. The code stays pending, and repeating the
        request finishes it.
    """
    final_url = policies.validated_final_url(
        request.code,
        request.destination,
        request.campaign,
        policies.code_name_errors(request.code) + policies.label_errors(request.label),
    )
    now = datetime.now(UTC)
    candidate = QrCode(
        code=request.code,
        kind=request.kind,
        destination=request.destination,
        final_url=final_url,
        label=request.label,
        campaign=request.campaign,
        owner_sub=actor.sub,
        created_by=actor.email,
        created_at=now,
        updated_by=actor.email,
        updated_at=now,
    )

    if deps.repository.add(candidate):
        logger.info("Code stored code=%s kind=%s", request.code, request.kind)
        return complete(request.code, deps), True

    existing = deps.repository.get(request.code)
    if existing is None or not _is_same_request(existing, request, actor):
        logger.info("Code name taken code=%s", request.code)
        raise CodeTaken

    logger.info("Create repeated, finishing any incomplete step code=%s", request.code)
    return complete(request.code, deps), False


def _is_same_request(existing: QrCode, request: CreateCodeRequest, actor: Actor) -> bool:
    """Tell a retry of the same request apart from a genuine name conflict."""
    return (
        existing.owner_sub == actor.sub
        and existing.kind == request.kind
        and existing.destination == request.destination
        and existing.label == request.label
        and existing.campaign == request.campaign
    )