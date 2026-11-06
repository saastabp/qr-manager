"""Business policies shared by the cases.

Who may act on a code, what input is acceptable, and how a code's status and allowed
actions are worked out. Clients display the results and never work them out themselves.
See docs/DESIGN.md, sections 3 and 8.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlsplit

from cases.errors import CodeNotFound, FieldError, InvalidInput
from cases.redirect_url import build_final_url
from common.logger import logger
from models.actor import Actor
from models.qr_code import CODE_PATTERN, CodeKind, CodeStatus, QrCode

#: Names kept free for possible future routes on the redirect domain.
RESERVED_CODES = frozenset({"admin", "api", "health"})

#: Room for the campaign and JSON, within the KeyValueStore's 1 KB limit for one value.
MAX_FINAL_URL_BYTES = 900

#: Printable ASCII without spaces, quotes, or backslashes. A URL like this is safe in a
#: Location header, and JSON encoding does not grow it, so the limit above holds.
URL_CHARACTERS = r"[!#-\[\]-~]+"

#: Longest label, in characters. Without a limit, a huge label would exceed DynamoDB's item
#: size and reach the caller as a 500.
MAX_LABEL_CHARACTERS = 200

#: Shown when a code or campaign does not follow the shared name format.
NAME_FORMAT_MESSAGE = (
    "Use 1 to 64 lowercase letters, digits, or hyphens, not starting or ending in a hyphen"
)


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
        return [FieldError("code", NAME_FORMAT_MESSAGE)]

    if code in RESERVED_CODES:
        return [FieldError("code", "Code is reserved")]

    return []


def destination_errors(destination: str) -> list[FieldError]:
    """Check a destination URL.

    Parameters
    ----------
    destination : str
        The URL entered by the user. Other characters must be percent-encoded.

    Returns
    -------
    list[FieldError]
        Any problems found; empty if the URL is acceptable.

    Examples
    --------
    >>> destination_errors("https://example.com/caf%C3%A9")
    []
    >>> destination_errors("https://example.com/a b")[0].field
    'destination'
    """
    if re.fullmatch(URL_CHARACTERS, destination) and _is_full_https(destination):
        return []

    message = "Destination must be a full https URL, with special characters percent-encoded"
    return [FieldError("destination", message)]


def _is_full_https(url: str) -> bool:
    """Tell whether a URL parses as https with a host."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False

    return parts.scheme == "https" and bool(parts.hostname)


def campaign_errors(campaign: str) -> list[FieldError]:
    """Check a campaign name.

    Campaigns follow the code format, which keeps them safe in the scan log format.

    Parameters
    ----------
    campaign : str
        The proposed campaign. Blank means no campaign.

    Returns
    -------
    list[FieldError]
        Any problems found; empty if the campaign is acceptable.

    Examples
    --------
    >>> campaign_errors("spring-2027")
    []
    >>> campaign_errors("")
    []
    >>> campaign_errors("Spring 2027")[0].field
    'campaign'
    """
    if campaign and not re.fullmatch(CODE_PATTERN, campaign):
        return [FieldError("campaign", NAME_FORMAT_MESSAGE)]

    return []


def label_errors(label: str) -> list[FieldError]:
    """Check a label's length.

    Parameters
    ----------
    label : str
        The proposed label. Blank means none.

    Returns
    -------
    list[FieldError]
        Any problems found; empty if the label is acceptable.
    """
    if len(label) > MAX_LABEL_CHARACTERS:
        message = f"Label must be {MAX_LABEL_CHARACTERS} characters or fewer"
        return [FieldError("label", message)]

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


def validated_final_url(
    code: str, destination: str, campaign: str, earlier_errors: Sequence[FieldError] = ()
) -> str:
    """Apply the URL rules and build a code's final URL.

    Parameters
    ----------
    code : str
        The code name.
    destination : str
        The URL entered by the user.
    campaign : str
        The campaign. Blank means none.
    earlier_errors : Sequence[FieldError]
        Problems the caller already found, so every problem is reported at once.

    Returns
    -------
    str
        The URL a scan should end up at.

    Raises
    ------
    InvalidInput
        If any rule is broken.
    """
    errors = [*earlier_errors, *destination_errors(destination), *campaign_errors(campaign)]
    if errors:
        raise InvalidInput(errors)

    final_url = build_final_url(code, destination, campaign)
    errors = final_url_errors(final_url)
    if errors:
        raise InvalidInput(errors)

    return final_url


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