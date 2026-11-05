"""Interfaces the cases use to reach the outside world.

Cases depend only on these. Adapters implement them with AWS, and tests implement them
with in-memory fakes. Python refuses to create a subclass that misses a method, so a gap
fails immediately rather than when the method is first called. See docs/DESIGN.md,
section 6.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from models.qr_code import QrCode


class CodeRepository(ABC):
    """Stores codes. This is the source of truth."""

    @abstractmethod
    def add(self, code: QrCode) -> bool:
        """Store a new code.

        Parameters
        ----------
        code : QrCode
            The code to store.

        Returns
        -------
        bool
            True if stored, False if the code name already exists.
        """

    @abstractmethod
    def get(self, code: str) -> QrCode | None:
        """Read a code, always returning the latest value.

        Parameters
        ----------
        code : str
            The code name.

        Returns
        -------
        QrCode or None
            The stored code, or None if it does not exist.
        """

    @abstractmethod
    def list_by_owner(self, owner_sub: str) -> list[QrCode]:
        """List one owner's codes, newest first.

        Parameters
        ----------
        owner_sub : str
            The owner's Cognito id.

        Returns
        -------
        list[QrCode]
            The owner's codes.
        """

    @abstractmethod
    def update(self, code: QrCode) -> None:
        """Replace a stored code.

        Parameters
        ----------
        code : QrCode
            The new value, with its version already increased.
        """

    @abstractmethod
    def mark_complete(self, code: str, version: int, *, synced: bool, rendered: bool) -> bool:
        """Record finished steps, only if nothing has changed since.

        Parameters
        ----------
        code : str
            The code name.
        version : int
            The version these steps were completed for.
        synced : bool
            New value of the synced flag.
        rendered : bool
            New value of the rendered flag.

        Returns
        -------
        bool
            True if recorded, False if a newer version exists.
        """


class RedirectStore(ABC):
    """The store the redirect function reads from."""

    @abstractmethod
    def publish(self, code: str, final_url: str, campaign: str) -> None:
        """Make a code redirect to a URL.

        Parameters
        ----------
        code : str
            The code name.
        final_url : str
            Where the code should redirect.
        campaign : str
            The code's campaign, logged with each scan. Blank means none.

        Raises
        ------
        ConnectionError
            If the store cannot be updated after retrying.
        """


class ImageStore(ABC):
    """Where rendered PNGs are kept."""

    @abstractmethod
    def exists(self, code: str, logo: str) -> bool:
        """Check whether a PNG is stored.

        Parameters
        ----------
        code : str
            The code name.
        logo : str
            The logo variant.

        Returns
        -------
        bool
            True if the PNG exists.
        """

    @abstractmethod
    def download_url(self, code: str, logo: str) -> str:
        """Create a short-lived URL for downloading a PNG.

        Parameters
        ----------
        code : str
            The code name.
        logo : str
            The logo variant.

        Returns
        -------
        str
            The presigned URL.
        """


class Renderer(ABC):
    """Creates and stores PNGs."""

    @abstractmethod
    def render(self, code: str, encoded_url: str, logo: str) -> None:
        """Render a PNG and store it, waiting for the result.

        Parameters
        ----------
        code : str
            The code name, which decides where the PNG is stored.
        encoded_url : str
            The URL the QR image encodes.
        logo : str
            The logo variant.

        Raises
        ------
        ConnectionError
            If rendering fails.
        """