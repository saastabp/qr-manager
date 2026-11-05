"""In-memory fakes of the ports, so business rules can be tested with no AWS."""

from __future__ import annotations

from collections.abc import Callable

from cases.completion import Dependencies
from cases.ports import CodeRepository, ImageStore, RedirectStore, Renderer
from models.actor import Actor
from models.qr_code import QrCode

REDIRECT_DOMAIN = "qr.example.com"

OWNER = Actor(sub="owner-sub", email="owner@example.com")
STRANGER = Actor(sub="stranger-sub", email="stranger@example.com")


class FakeRepository(CodeRepository):
    """Stores codes in a dictionary."""

    def __init__(self) -> None:
        self.codes: dict[str, QrCode] = {}
        # Runs just before mark_complete, to simulate a concurrent writer.
        self.before_mark: Callable[[], None] | None = None

    def add(self, code: QrCode) -> bool:
        """Store a new code, refusing a name that exists."""
        if code.code in self.codes:
            return False

        self.codes[code.code] = code
        return True

    def get(self, code: str) -> QrCode | None:
        """Read a code."""
        return self.codes.get(code)

    def list_by_owner(self, owner_sub: str) -> list[QrCode]:
        """List one owner's codes, newest first."""
        owned = [code for code in self.codes.values() if code.owner_sub == owner_sub]
        return sorted(owned, key=lambda code: code.created_at, reverse=True)

    def update(self, code: QrCode) -> bool:
        """Replace a stored code, only if the version is the next one."""
        if self.codes[code.code].version != code.version - 1:
            return False

        self.codes[code.code] = code
        return True

    def mark_complete(self, code: str, version: int, *, synced: bool, rendered: bool) -> bool:
        """Record finished steps, only if the version still matches."""
        if self.before_mark is not None:
            hook = self.before_mark
            self.before_mark = None
            hook()

        stored = self.codes[code]
        if stored.version != version:
            return False

        self.codes[code] = stored.model_copy(update={"synced": synced, "rendered": rendered})
        return True


class FakeRedirects(RedirectStore):
    """Records what was published."""

    def __init__(self) -> None:
        self.published: dict[str, str] = {}
        self.campaigns: dict[str, str] = {}
        self.calls = 0
        self.fail = False

    def publish(self, code: str, final_url: str, campaign: str) -> None:
        """Record a publish, or fail if told to."""
        self.calls += 1
        if self.fail:
            raise ConnectionError("KVS unavailable")

        self.published[code] = final_url
        self.campaigns[code] = campaign


class FakeImages(ImageStore):
    """Tracks which PNGs exist."""

    def __init__(self) -> None:
        self.stored: set[tuple[str, str]] = set()

    def exists(self, code: str, logo: str) -> bool:
        """Check whether a PNG is stored."""
        return (code, logo) in self.stored

    def download_url(self, code: str, logo: str) -> str:
        """Return a predictable URL."""
        return f"https://images.example.com/{code}/{logo}.png"


class FakeRenderer(Renderer):
    """Records renders and stores the result in the fake image store."""

    def __init__(self, images: FakeImages) -> None:
        self.images = images
        self.calls: list[tuple[str, str, str]] = []
        self.fail = False

    def render(self, code: str, encoded_url: str, logo: str) -> None:
        """Record a render, or fail if told to."""
        self.calls.append((code, encoded_url, logo))
        if self.fail:
            raise ConnectionError("Render unavailable")

        self.images.stored.add((code, logo))


class FakeWorld:
    """One set of fakes, with the Dependencies the cases expect."""

    def __init__(self) -> None:
        self.repository = FakeRepository()
        self.redirects = FakeRedirects()
        self.images = FakeImages()
        self.renderer = FakeRenderer(self.images)
        self.deps = Dependencies(
            repository=self.repository,
            redirects=self.redirects,
            images=self.images,
            renderer=self.renderer,
            redirect_domain=REDIRECT_DOMAIN,
        )