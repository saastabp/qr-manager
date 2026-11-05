"""The authenticated caller.

Data shape only. Handlers build this from the verified token, so cases never see a JWT.
See docs/DESIGN.md, section 4.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Actor(BaseModel):
    """The person making a request.

    Parameters
    ----------
    sub : str
        Stable Cognito id. Matched against a code's owner.
    email : str
        Email address, recorded on changes for a readable audit trail.
    groups : tuple[str, ...]
        Cognito groups. Unused in Phase I, and kept for a later admins group.
    """

    model_config = ConfigDict(frozen=True)

    sub: str
    email: str
    groups: tuple[str, ...] = ()