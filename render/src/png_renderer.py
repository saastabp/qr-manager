"""Draws a branded QR code PNG.

Navy square modules, terracotta rounded finder eyes, a centered logo, and a rounded
terracotta frame on a warm cream background. Renders at SS times the final size, then
downsamples for clean curves. Uses error correction level H, so about 30% of the code can
be covered by the logo. See docs/DESIGN.md, section 5.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import qrcode
from PIL import Image, ImageChops, ImageDraw

NAVY = (0x1F, 0x3B, 0x4D)
TERRA = (0xC2, 0x48, 0x3A)
CREAM = (0xFB, 0xF8, 0xF2)
WHITE = (255, 255, 255)

FINAL = 2048  # Output size in pixels, about 6.8 inches at 300 DPI
SS = 2  # Supersampling factor
QUIET = 4  # Quiet zone in modules, the QR spec minimum
LOGO_FRAC = 0.34  # Logo clear zone, as a fraction of the QR width


def load_logo(path: Path) -> Image.Image:
    """Open a logo and trim the white or transparent space around it.

    Parameters
    ----------
    path : Path
        The logo image.

    Returns
    -------
    Image.Image
        The trimmed logo, in RGBA.
    """
    logo = Image.open(path).convert("RGBA")
    background = Image.new("RGBA", logo.size, WHITE + (255,))
    diff = ImageChops.difference(
        Image.alpha_composite(background, logo).convert("RGB"),
        Image.new("RGB", logo.size, WHITE),
    )
    bbox = diff.point(lambda p: 255 if p > 20 else 0).getbbox()
    if bbox:
        return logo.crop(bbox)

    return logo


def render_png(url: str, logo_path: Path) -> bytes:
    """Draw a branded QR code.

    Parameters
    ----------
    url : str
        The text to encode.
    logo_path : Path
        The logo to place in the center.

    Returns
    -------
    bytes
        A 2048 by 2048 PNG at 300 DPI.

    Raises
    ------
    qrcode.exceptions.DataOverflowError
        If the URL is too long for a QR code.
    """
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=0)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)

    # Layout: frame border, then quiet zone, then modules.
    canvas = FINAL * SS
    frame_w = canvas // 40
    inner = canvas - 2 * frame_w
    mod = inner / (n + 2 * QUIET)
    origin = frame_w + QUIET * mod

    img = Image.new("RGB", (canvas, canvas), CREAM)
    draw = ImageDraw.Draw(img)

    # Rounded terracotta frame.
    radius = canvas // 12
    draw.rounded_rectangle([0, 0, canvas - 1, canvas - 1], radius=radius, fill=TERRA)
    draw.rounded_rectangle(
        [frame_w, frame_w, canvas - 1 - frame_w, canvas - 1 - frame_w],
        radius=radius - frame_w,
        fill=CREAM,
    )

    # Logo clear zone in modules, centered on the module grid.
    logo_mods = int(round(n * LOGO_FRAC))
    if logo_mods % 2 != n % 2:
        logo_mods += 1
    low = (n - logo_mods) // 2
    high = low + logo_mods

    def in_finder(r: int, c: int) -> bool:
        return (r < 7 and c < 7) or (r < 7 and c >= n - 7) or (r >= n - 7 and c < 7)

    def in_logo(r: int, c: int) -> bool:
        return low <= r < high and low <= c < high

    # Data modules as full navy squares. Edges snap to whole pixels, so neighbours abut
    # without hairline seams.
    for r in range(n):
        for c in range(n):
            if not matrix[r][c] or in_finder(r, c) or in_logo(r, c):
                continue
            x0 = round(origin + c * mod)
            y0 = round(origin + r * mod)
            x1 = round(origin + (c + 1) * mod) - 1
            y1 = round(origin + (r + 1) * mod) - 1
            draw.rectangle([x0, y0, x1, y1], fill=NAVY)

    # Finder eyes: terracotta rounded ring, navy rounded center.
    def eye(r0: int, c0: int) -> None:
        x, y = origin + c0 * mod, origin + r0 * mod
        size = 7 * mod
        draw.rounded_rectangle([x, y, x + size, y + size], radius=size * 0.32, fill=TERRA)
        draw.rounded_rectangle(
            [x + mod, y + mod, x + size - mod, y + size - mod],
            radius=(size - 2 * mod) * 0.28,
            fill=CREAM,
        )
        draw.rounded_rectangle(
            [x + 2 * mod, y + 2 * mod, x + size - 2 * mod, y + size - 2 * mod],
            radius=3 * mod * 0.3,
            fill=NAVY,
        )

    eye(0, 0)
    eye(0, n - 7)
    eye(n - 7, 0)

    # Logo: fit inside the clear zone on a rounded cream card.
    logo = load_logo(logo_path)
    zone = logo_mods * mod
    zone_xy = origin + low * mod
    card_pad = mod * 0.6
    draw.rounded_rectangle(
        [
            zone_xy + card_pad * 0.3,
            zone_xy + card_pad * 0.3,
            zone_xy + zone - card_pad * 0.3,
            zone_xy + zone - card_pad * 0.3,
        ],
        radius=zone * 0.18,
        fill=CREAM,
    )
    available = zone - 2 * card_pad
    scale = min(available / logo.width, available / logo.height)
    width, height = int(logo.width * scale), int(logo.height * scale)
    logo = logo.resize((width, height), Image.LANCZOS)

    # Make a near-white logo background transparent so it sits on the cream card.
    pixels = logo.load()
    for y in range(height):
        for x in range(width):
            red, green, blue, _ = pixels[x, y]
            if red > 245 and green > 245 and blue > 245:
                pixels[x, y] = (red, green, blue, 0)

    position = (int(zone_xy + (zone - width) / 2), int(zone_xy + (zone - height) / 2))
    img.paste(logo, position, logo)

    img = img.resize((FINAL, FINAL), Image.LANCZOS)
    output = BytesIO()
    img.save(output, format="PNG", dpi=(300, 300))
    return output.getvalue()