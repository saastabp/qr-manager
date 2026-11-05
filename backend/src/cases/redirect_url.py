"""URL rules: what a code redirects to, and what its image encodes.

See docs/DESIGN.md, section 3.
"""

from __future__ import annotations

from urllib.parse import urlencode, urlsplit, urlunsplit

from models.qr_code import CodeKind


def build_final_url(code: str, destination: str, campaign: str) -> str:
    """Add UTM parameters to a destination when a campaign is set.

    Parameters
    ----------
    code : str
        The code name, recorded as ``utm_content``.
    destination : str
        URL entered by the user.
    campaign : str
        Campaign name. Blank leaves the destination unchanged.

    Returns
    -------
    str
        The URL a scan should end up at.

    Examples
    --------
    >>> build_final_url("bldc-flyer", "https://example.com/a", "bldc")
    'https://example.com/a?utm_source=qr&utm_medium=print&utm_campaign=bldc&utm_content=bldc-flyer'
    >>> build_final_url("one-off", "https://example.com/a", "")
    'https://example.com/a'
    """
    if not campaign:
        return destination

    utm = urlencode(
        {
            "utm_source": "qr",
            "utm_medium": "print",
            "utm_campaign": campaign,
            "utm_content": code,
        }
    )
    parts = urlsplit(destination)

    # Keep any existing query exactly as the user entered it.
    query = utm
    if parts.query:
        query = f"{parts.query}&{utm}"

    return urlunsplit(parts._replace(query=query))


def build_encoded_url(code: str, kind: CodeKind, final_url: str, redirect_domain: str) -> str:
    """Work out the URL a code's image encodes.

    Parameters
    ----------
    code : str
        The code name.
    kind : CodeKind
        Static or dynamic.
    final_url : str
        The code's final URL.
    redirect_domain : str
        Hostname of the redirect service, such as ``qr.360balancedliving.com``.

    Returns
    -------
    str
        The final URL for a static code, or the short URL for a dynamic one.
    """
    if kind == CodeKind.STATIC:
        return final_url

    return f"https://{redirect_domain}/{code}"