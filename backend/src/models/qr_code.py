"""The QR code entity and the types that describe it.

Data shapes only. Business rules live in cases. See docs/DESIGN.md, section 4.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

#: Structural format of a code. Must match CODE_FORMAT in the redirect function.
CODE_PATTERN = r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$"


class CodeKind(StrEnum):
    """Whether a code's destination can change after printing."""

    STATIC = "static"
    DYNAMIC = "dynamic"


class CodeStatus(StrEnum):
    """Readiness of a code, as reported to clients."""

    STATIC = "static"
    LIVE = "live"
    PENDING = "pending"


class QrCode(BaseModel):
    """A registered QR code.

    Parameters
    ----------
    code : str
        Unique name, which is also the URL path of a dynamic code.
    kind : CodeKind
        Static or dynamic. Never changes after creation.
    destination : str
        URL entered by the user.
    final_url : str
        Destination plus UTM parameters when a campaign is set.
    label : str
        Optional human-readable description.
    campaign : str
        Optional campaign name. Blank means no UTM parameters.
    owner_sub : str
        Stable Cognito id of the owner.
    created_by : str
        Email of the creator.
    created_at : datetime
        When the code was created.
    updated_by : str
        Email of the last person to change it.
    updated_at : datetime
        When it last changed.
    version : int
        Increases on every change, for optimistic concurrency.
    synced : bool
        Whether the redirect store has the current final URL. Dynamic codes only.
    rendered : bool
        Whether the default PNG is stored.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(pattern=CODE_PATTERN)
    kind: CodeKind
    destination: str
    final_url: str
    label: str = ""
    campaign: str = ""
    owner_sub: str
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    version: int = 1
    synced: bool = False
    rendered: bool = False