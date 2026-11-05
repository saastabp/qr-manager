"""Business policies shared by the cases.

Who may act on a code, what input is acceptable, and how a code's status and allowed
actions are worked out. Clients display the results and never work them out themselves.
See docs/DESIGN.md, sections 3 and 8.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from cases.errors import CodeNotFound, FieldError
from common.logger import logger
from models.actor import Actor
from models.qr_code import CODE_PATTERN, CodeKind, CodeStatus, QrCode

#: Names kept free for possible future routes on the redirect domain.
RESERVED_CODES = frozenset({"admin", "api", "health"})

#: The KeyValueStore limit for one value.
MAX_FINAL_URL_BYTES = 1024


def code_name_errors(code: str) -> list[FieldError]:
    """Check a proposed code name.

    Parameters
    ----------
    code : str
        The proposed name.

    Returns
    -------
    list[FieldError]
        Any problems found; empty if the name is acceptable.
    """
    if not re.fullmatch(CODE_PATTERN, code):
        message = (
            "Use 1 to 64 lowercase letters, digits, or hyphens, "
            "not starting or ending in a hyphen"
        )
        return [FieldError("code", message)]

    if code in RESERVED_CODES:
        return [FieldError("code", "Code is reserved")]

    return []


def destination_errors(destination: str) -> list[FieldError]:
    """Check a destination URL.

    Parameters
    ----------
    destination : str
        The URL entered by the user.

    Returns
    -------
    list[FieldError]
        Any problems found; empty if the URL is acceptable.
    """
    parts = urlsplit(destination)
    if parts.scheme != "https" or not parts.netloc:
        return [FieldError("destination", "Destination must be a full https URL")]

    return []


def final_url_errors(final_url: str) -> list[FieldError]:
    """Check that a final URL fits in the redirect store.

    Parameters
    ----------
    final_url : str
        The destination with any UTM parameters added.

    Returns
    -------
    list[FieldError]
        Any problems found; empty if the URL fits.
    """
    if len(final_url.encode()) > MAX_FINAL_URL_BYTES:
        return [FieldError("destination", "Destination is too long")]

    return []


def status_of(code: QrCode) -> CodeStatus:
    """Work out a code's status.

    A code is ready only when its PNG exists and, for dynamic codes, its redirect is
    published.

    Parameters
    ----------
    code : QrCode
        The code.

    Returns
    -------
    CodeStatus
        ``pending`` until ready, then ``static`` or ``live``.
    """
    ready = code.rendered
    if code.kind == CodeKind.DYNAMIC:
        ready = ready and code.synced

    if not ready:
        return CodeStatus.PENDING

    if code.kind == CodeKind.STATIC:
        return CodeStatus.STATIC

    return CodeStatus.LIVE


def allowed_actions(code: QrCode) -> list[str]:
    """List what the owner can do with a code right now.

    Parameters
    ----------
    code : QrCode
        The code.

    Returns
    -------
    list[str]
        Action names. ``download`` is left out while the code is pending.
    """
    if status_of(code) == CodeStatus.PENDING:
        return ["update"]

    return ["update", "download"]


def authorize(actor: Actor, code: QrCode | None, action: str) -> QrCode:
    """Confirm the actor owns a code, and log the decision.

    Parameters
    ----------
    actor : Actor
        The caller.
    code : QrCode or None
        The stored code, or None if it does not exist.
    action : str
        What the caller is trying to do, for the log.

    Returns
    -------
    QrCode
        The code, so callers can continue with it.

    Raises
    ------
    CodeNotFound
        If the code is missing or belongs to someone else. The two are not distinguished,
        so the API never reveals another user's code.
    """
    if code is None:
        logger.info("Authorization denied action=%s reason=missing", action)
        raise CodeNotFound

    if code.owner_sub != actor.sub:
        logger.warning(
            "Authorization denied action=%s code=%s actor=%s reason=not_owner",
            action,
            code.code,
            actor.sub,
        )
        raise CodeNotFound

    logger.info(
        "Authorization allowed action=%s code=%s actor=%s reason=owner",
        action,
        code.code,
        actor.sub,
    )
    return code