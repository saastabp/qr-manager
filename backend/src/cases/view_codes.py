"""BC3: view your own codes. See docs/DESIGN.md, sections 3 and 8."""

from __future__ import annotations

from dataclasses import dataclass

from cases import policies
from cases.completion import Dependencies
from cases.redirect_url import build_encoded_url
from models.actor import Actor
from models.qr_code import CodeStatus, QrCode


@dataclass(frozen=True)
class CodeView:
    """A code plus everything a client needs, so clients decide nothing themselves.

    Parameters
    ----------
    code : QrCode
        The stored code.
    status : CodeStatus
        Whether the code is ready.
    encoded_url : str
        The URL its image encodes.
    allowed_actions : list[str]
        What the owner can do right now.
    """

    code: QrCode
    status: CodeStatus
    encoded_url: str
    allowed_actions: list[str]


def view_of(code: QrCode, deps: Dependencies) -> CodeView:
    """Build the client view of a code.

    Parameters
    ----------
    code : QrCode
        The code.
    deps : Dependencies
        The ports and settings.

    Returns
    -------
    CodeView
        The code with its derived values.
    """
    return CodeView(
        code=code,
        status=policies.status_of(code),
        encoded_url=build_encoded_url(code.code, code.kind, code.final_url, deps.redirect_domain),
        allowed_actions=policies.allowed_actions(code),
    )


def list_codes(actor: Actor, deps: Dependencies) -> list[CodeView]:
    """List the caller's codes, newest first.

    Parameters
    ----------
    actor : Actor
        The caller.
    deps : Dependencies
        The ports and settings.

    Returns
    -------
    list[CodeView]
        The caller's codes.
    """
    return [view_of(code, deps) for code in deps.repository.list_by_owner(actor.sub)]


def get_code(name: str, actor: Actor, deps: Dependencies) -> CodeView:
    """Read one of the caller's codes.

    Parameters
    ----------
    name : str
        The code name.
    actor : Actor
        The caller.
    deps : Dependencies
        The ports and settings.

    Returns
    -------
    CodeView
        The code with its derived values.

    Raises
    ------
    CodeNotFound
        If the code is missing or belongs to someone else.
    """
    code = policies.authorize(actor, deps.repository.get(name), "read")
    return view_of(code, deps)