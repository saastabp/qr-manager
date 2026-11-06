"""Business failures raised by the cases.

These say what went wrong in business terms. Handlers map them to HTTP status codes, so
nothing here knows about HTTP. See docs/DESIGN.md, section 7.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldError:
    """A problem with one input field.

    Parameters
    ----------
    field : str
        Name of the input field.
    message : str
        What is wrong, in words a client can show to a person.
    """

    field: str
    message: str


class CaseError(Exception):
    """Base class for every business failure."""


class InvalidInput(CaseError):
    """The request broke a business rule.

    Parameters
    ----------
    errors : list[FieldError]
        One entry per field that failed.
    """

    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__(errors[0].message)
        self.errors = errors


class CodeTaken(CaseError):
    """The code name already belongs to a different request."""


class CodeNotFound(CaseError):
    """The code does not exist, or the caller does not own it.

    The two look the same on purpose, so the API never reveals another user's code.
    """


class CodeNotReady(CaseError):
    """The code is still pending, so its image cannot be downloaded yet."""


class IdentityMissing(CaseError):
    """The token is genuine but does not say who the caller is, as with an access token."""


class CodeConflict(CaseError):
    """The code changed after this request read it, so the change was not saved."""


class StepFailed(CaseError):
    """A step that reaches another service failed, leaving the code pending.

    Parameters
    ----------
    step : str
        Which step failed: ``publish`` or ``render``.
    """

    def __init__(self, step: str) -> None:
        super().__init__(f"The {step} step failed")
        self.step = step