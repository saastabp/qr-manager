"""Finishes a code: publishes its redirect and renders its PNG.

Shared by create, change, and resync, so a retry behaves the same everywhere. Every step
is safe to repeat. See docs/DESIGN.md, section 5.
"""

from __future__ import annotations

from dataclasses import dataclass

from cases.errors import CodeNotFound, StepFailed
from cases.ports import CodeRepository, ImageStore, RedirectStore, Renderer
from cases.redirect_url import build_encoded_url
from common.logger import logger
from models.qr_code import CodeKind, QrCode

#: The logo variant rendered when a code is created.
DEFAULT_LOGO = "full"

#: How many times to re-read and retry if the code changes while finishing.
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Dependencies:
    """The ports and settings the cases need.

    Parameters
    ----------
    repository : CodeRepository
        Where codes are stored.
    redirects : RedirectStore
        The store the redirect function reads.
    images : ImageStore
        Where PNGs are kept.
    renderer : Renderer
        Creates PNGs.
    redirect_domain : str
        Hostname of the redirect service.
    """

    repository: CodeRepository
    redirects: RedirectStore
    images: ImageStore
    renderer: Renderer
    redirect_domain: str


def complete(name: str, deps: Dependencies, *, republish: bool = False) -> QrCode:
    """Finish whichever steps a code still needs.

    Each attempt re-reads the code, so a retry cannot publish a stale value. If the code
    changed while finishing, the next attempt publishes the newer value even if it is
    marked as synced, because this attempt may have overwritten it.

    Parameters
    ----------
    name : str
        The code name.
    deps : Dependencies
        The ports and settings.
    republish : bool
        Publish even if the code is already marked as synced.

    Returns
    -------
    QrCode
        The finished code.

    Raises
    ------
    CodeNotFound
        If the code does not exist.
    StepFailed
        If publishing or rendering fails. The code stays pending and can be retried.
    """
    for _ in range(MAX_ATTEMPTS):
        code = deps.repository.get(name)
        if code is None:
            raise CodeNotFound

        synced = _publish_if_needed(code, deps, republish=republish)
        if not code.rendered:
            render_image(code, DEFAULT_LOGO, deps)

        if deps.repository.mark_complete(name, code.version, synced=synced, rendered=True):
            logger.info("Code complete code=%s version=%s", name, code.version)
            return code.model_copy(update={"synced": synced, "rendered": True})

        # A newer version exists, and this attempt may have overwritten its redirect.
        logger.info("Code changed while finishing code=%s version=%s", name, code.version)
        republish = True

    # The wording matters: the step-failures alarm matches "Publish failed".
    logger.error("Publish failed code=%s reason=kept_changing", name)
    raise StepFailed("publish")


def render_image(code: QrCode, logo: str, deps: Dependencies) -> None:
    """Render one of a code's PNGs and store it.

    Parameters
    ----------
    code : QrCode
        The code.
    logo : str
        The logo variant.
    deps : Dependencies
        The ports and settings.

    Raises
    ------
    StepFailed
        If rendering fails. The step-failures alarm matches the "Render failed" log line.
    """
    encoded_url = build_encoded_url(code.code, code.kind, code.final_url, deps.redirect_domain)
    try:
        deps.renderer.render(code.code, encoded_url, logo)
    except ConnectionError:
        logger.exception(
            "Render failed code=%s logo=%s version=%s", code.code, logo, code.version
        )
        raise StepFailed("render") from None


def _publish_if_needed(code: QrCode, deps: Dependencies, *, republish: bool) -> bool:
    """Publish a dynamic code if needed, and return the new synced value."""
    if code.kind != CodeKind.DYNAMIC:
        return False

    if code.synced and not republish:
        return True

    try:
        deps.redirects.publish(code.code, code.final_url, code.campaign)
    except ConnectionError:
        logger.exception("Publish failed code=%s version=%s", code.code, code.version)
        raise StepFailed("publish") from None

    return True