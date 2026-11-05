"""BC4: get the download URL for a code's PNG. See docs/DESIGN.md, section 3."""

from __future__ import annotations

from cases import policies
from cases.completion import Dependencies
from cases.errors import CodeNotReady, FieldError, InvalidInput, StepFailed
from cases.redirect_url import build_encoded_url
from common.logger import logger
from models.actor import Actor
from models.qr_code import CodeStatus

#: The logo variants a code's image can use.
LOGO_VARIANTS = frozenset({"flower", "full"})


def get_image(name: str, logo: str, actor: Actor, deps: Dependencies) -> str:
    """Return a download URL, rendering the PNG first if it does not exist yet.

    Parameters
    ----------
    name : str
        The code name.
    logo : str
        The logo variant, ``full`` or ``flower``.
    actor : Actor
        The caller, who must own the code.
    deps : Dependencies
        The ports and settings.

    Returns
    -------
    str
        A short-lived URL for downloading the PNG.

    Raises
    ------
    CodeNotFound
        If the code is missing or belongs to someone else.
    InvalidInput
        If the logo variant is unknown.
    CodeNotReady
        If the code is pending, so nobody can print a code that is not ready.
    StepFailed
        If rendering fails. Asking again retries it.
    """
    code = policies.authorize(actor, deps.repository.get(name), "download")

    if logo not in LOGO_VARIANTS:
        raise InvalidInput([FieldError("logo", "Logo must be full or flower")])

    if policies.status_of(code) == CodeStatus.PENDING:
        logger.info("Image refused while pending code=%s", name)
        raise CodeNotReady

    if not deps.images.exists(name, logo):
        encoded_url = build_encoded_url(name, code.kind, code.final_url, deps.redirect_domain)
        try:
            deps.renderer.render(name, encoded_url, logo)
        except ConnectionError:
            logger.exception("Render failed code=%s logo=%s", name, logo)
            raise StepFailed("render") from None

    return deps.images.download_url(name, logo)