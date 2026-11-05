"""Request and response shapes for the HTTP API.

The API's own shapes, kept separate from the models, so internal fields never reach a
client. See docs/DESIGN.md, section 7.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from cases.view_codes import CodeView
from models.qr_code import CodeKind


class CreateCodeBody(BaseModel):
    """The body of ``POST /codes``.

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
        Optional campaign name.
    """

    # Unknown fields are refused, so a typing mistake is not silently ignored.
    model_config = ConfigDict(extra="forbid")

    code: str
    kind: CodeKind
    destination: str
    label: str = ""
    campaign: str = ""


class ChangeCodeBody(BaseModel):
    """The body of ``PATCH /codes/{code}``. Omitted fields are left alone.

    Parameters
    ----------
    destination : str or None
        New destination.
    label : str or None
        New description.
    campaign : str or None
        New campaign. Blank removes it.
    """

    # This also refuses an attempt to change kind, which has no field here.
    model_config = ConfigDict(extra="forbid")

    destination: str | None = None
    label: str | None = None
    campaign: str | None = None


def code_response(view: CodeView) -> dict[str, Any]:
    """Build the JSON shape of a code.

    Parameters
    ----------
    view : CodeView
        The code and its derived values.

    Returns
    -------
    dict[str, Any]
        The response body. Internal fields are left out.
    """
    code = view.code
    return {
        "code": code.code,
        "kind": code.kind.value,
        "status": view.status.value,
        "encoded_url": view.encoded_url,
        "destination": code.destination,
        "final_url": code.final_url,
        "label": code.label,
        "campaign": code.campaign,
        "allowed_actions": view.allowed_actions,
        "updated_by": code.updated_by,
        "updated_at": code.updated_at.isoformat(),
    }