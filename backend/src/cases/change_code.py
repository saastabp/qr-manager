"""BC2: change a code you own. See docs/DESIGN.md, section 3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cases import policies
from cases.completion import Dependencies, complete
from cases.errors import CodeConflict, FieldError, InvalidInput
from common.logger import logger
from models.actor import Actor
from models.qr_code import CodeKind, QrCode


@dataclass(frozen=True)
class ChangeCodeRequest:
    """What a caller provides to change a code. None means leave the field alone.

    Parameters
    ----------
    code : str
        The code to change.
    destination : str or None
        New destination.
    label : str or None
        New description.
    campaign : str or None
        New campaign. Blank removes it.
    """

    code: str
    destination: str | None = None
    label: str | None = None
    campaign: str | None = None


def change_code(request: ChangeCodeRequest, actor: Actor, deps: Dependencies) -> QrCode:
    """Apply a change, or finish and republish a code if nothing was sent.

    Parameters
    ----------
    request : ChangeCodeRequest
        What the caller provided.
    actor : Actor
        The caller, who must own the code.
    deps : Dependencies
        The ports and settings.

    Returns
    -------
    QrCode
        The finished code.

    Raises
    ------
    CodeNotFound
        If the code is missing or belongs to someone else.
    InvalidInput
        If the change breaks a business rule.
    CodeConflict
        If the code changed after it was read. Nothing is saved, and the caller can retry.
    StepFailed
        If publishing or rendering fails. The code stays pending and can be retried.
    """
    existing = policies.authorize(actor, deps.repository.get(request.code), "update")

    if _is_empty(request):
        logger.info("Empty change, finishing and republishing code=%s", request.code)
        return complete(request.code, deps, republish=True)

    destination = _chosen(request.destination, existing.destination)
    campaign = _chosen(request.campaign, existing.campaign)
    label = _chosen(request.label, existing.label)

    _reject_static_url_change(existing, destination, campaign)

    final_url = policies.validated_final_url(existing.code, destination, campaign)

    changed = existing.model_copy(
        update={
            "destination": destination,
            "campaign": campaign,
            "label": label,
            "final_url": final_url,
            "updated_by": actor.email,
            "updated_at": datetime.now(UTC),
            "version": existing.version + 1,
            # A label change leaves the published redirect correct.
            "synced": existing.synced and final_url == existing.final_url,
        }
    )
    if not deps.repository.update(changed):
        logger.info("Change refused, code changed since read code=%s", changed.code)
        raise CodeConflict

    logger.info("Code updated code=%s version=%s", changed.code, changed.version)

    return complete(changed.code, deps)


def _is_empty(request: ChangeCodeRequest) -> bool:
    """Tell whether the request changes nothing."""
    return request.destination is None and request.label is None and request.campaign is None


def _chosen(requested: str | None, current: str) -> str:
    """Return the requested value, or the current one if none was sent."""
    if requested is None:
        return current

    return requested


def _reject_static_url_change(existing: QrCode, destination: str, campaign: str) -> None:
    """Refuse a change to what a static code's printed image encodes."""
    if existing.kind != CodeKind.STATIC:
        return

    errors = []
    if destination != existing.destination:
        errors.append(FieldError("destination", "A static code's destination cannot change"))
    if campaign != existing.campaign:
        errors.append(FieldError("campaign", "A static code's campaign cannot change"))

    if errors:
        raise InvalidInput(errors)