"""Draws a branded QR code PNG.

Navy square modules, terracotta rounded finder eyes, a centered logo, and a rounded
terracotta frame on a warm cream background. Renders at SS times the final size, then
downsamples for clean curves. Uses error correction level H, so about 30% of the code can
be covered by the logo. See docs/DESIGN.md, section 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import qrcode
from PIL import Image, ImageChops, ImageDraw

NAVY = (0x1F, 0x3B, 0x4D)
TERRA = (0xC2, 0x48, 0x3A)
CREAM = (0xFB, 0xF8, 0xF2)
WHITE = (255, 255, 255)

FINAL = 2048  # Output size in pixels, about 6.8 inches at 300 DPI
DPI = 300  # Print resolution stored in the PNG
SS = 2  # Supersampling factor
QUIET = 4  # Quiet zone in modules, the QR spec minimum
FINDER_MODULES = 7  # Width of a finder pattern, fixed by the QR spec
FINDER_CENTER_MODULES = 3  # Width of a finder pattern's center, fixed by the QR spec

FRAME_DIVISOR = 40  # Frame width, as a fraction of the canvas: 1/40
CORNER_DIVISOR = 12  # Frame corner radius, as a fraction of the canvas: 1/12

# How round each part of a finder eye is, as a fraction of its width.
EYE_OUTER_ROUNDING = 0.32
EYE_RING_ROUNDING = 0.28
EYE_CENTER_ROUNDING = 0.3

LOGO_FRAC = 0.34  # Logo clear zone, as a fraction of the QR width
CARD_PAD_MODULES = 0.6  # Space between the logo and the clear zone, in modules
CARD_INSET = 0.3  # How far the card sits inside the clear zone, as a fraction of the pad
CARD_ROUNDING = 0.18  # How round the card is, as a fraction of the clear zone

TRIM_THRESHOLD = 20  # Colour difference from white that counts as logo, when trimming
NEAR_WHITE = 245  # Channel value above which a logo pixel counts as background


@dataclass(frozen=True)
class _Grid:
    """Where the QR modules sit on the canvas."""

    size: int  # Modules along one side
    module: float  # Width of one module, in pixels
    origin: float  # Pixel position of the first module, on both axes


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
    bbox = diff.point(lambda p: 255 if p > TRIM_THRESHOLD else 0).getbbox()
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
    size = len(matrix)

    # Layout: frame border, then quiet zone, then modules.
    canvas = FINAL * SS
    frame_width = canvas // FRAME_DIVISOR
    module = (canvas - 2 * frame_width) / (size + 2 * QUIET)
    grid = _Grid(size=size, module=module, origin=frame_width + QUIET * module)

    # Logo clear zone in modules, centered on the module grid.
    logo_modules = int(round(size * LOGO_FRAC))
    if logo_modules % 2 != size % 2:
        logo_modules += 1
    logo_start = (size - logo_modules) // 2

    img = Image.new("RGB", (canvas, canvas), CREAM)
    draw = ImageDraw.Draw(img)

    _draw_frame(draw, canvas, frame_width)
    _draw_modules(draw, matrix, grid, logo_start, logo_modules)
    _draw_eye(draw, grid, 0, 0)
    _draw_eye(draw, grid, 0, size - FINDER_MODULES)
    _draw_eye(draw, grid, size - FINDER_MODULES, 0)
    _place_logo(img, draw, logo_path, grid, logo_start, logo_modules)

    img = img.resize((FINAL, FINAL), Image.LANCZOS)
    output = BytesIO()
    img.save(output, format="PNG", dpi=(DPI, DPI))
    return output.getvalue()


def _draw_frame(draw: ImageDraw.ImageDraw, canvas: int, frame_width: int) -> None:
    """Draw the rounded terracotta frame."""
    radius = canvas // CORNER_DIVISOR
    draw.rounded_rectangle([0, 0, canvas - 1, canvas - 1], radius=radius, fill=TERRA)
    draw.rounded_rectangle(
        [frame_width, frame_width, canvas - 1 - frame_width, canvas - 1 - frame_width],
        radius=radius - frame_width,
        fill=CREAM,
    )


def _in_finder(row: int, column: int, size: int) -> bool:
    """Tell whether a module belongs to one of the three finder patterns."""
    near_start = size - FINDER_MODULES
    return (
        (row < FINDER_MODULES and column < FINDER_MODULES)
        or (row < FINDER_MODULES and column >= near_start)
        or (row >= near_start and column < FINDER_MODULES)
    )


def _draw_modules(
    draw: ImageDraw.ImageDraw,
    matrix: list[list[bool]],
    grid: _Grid,
    logo_start: int,
    logo_modules: int,
) -> None:
    """Draw data modules as full navy squares, leaving out the finders and logo zone.

    Edges snap to whole pixels, so neighbours abut without hairline seams.
    """
    logo_end = logo_start + logo_modules
    for row in range(grid.size):
        for column in range(grid.size):
            in_logo = logo_start <= row < logo_end and logo_start <= column < logo_end
            if not matrix[row][column] or in_logo or _in_finder(row, column, grid.size):
                continue
            x0 = round(grid.origin + column * grid.module)
            y0 = round(grid.origin + row * grid.module)
            x1 = round(grid.origin + (column + 1) * grid.module) - 1
            y1 = round(grid.origin + (row + 1) * grid.module) - 1
            draw.rectangle([x0, y0, x1, y1], fill=NAVY)


def _draw_eye(draw: ImageDraw.ImageDraw, grid: _Grid, row: int, column: int) -> None:
    """Draw one finder eye: a terracotta rounded ring with a navy rounded center."""
    module = grid.module
    x, y = grid.origin + column * module, grid.origin + row * module
    size = FINDER_MODULES * module
    draw.rounded_rectangle(
        [x, y, x + size, y + size], radius=size * EYE_OUTER_ROUNDING, fill=TERRA
    )
    draw.rounded_rectangle(
        [x + module, y + module, x + size - module, y + size - module],
        radius=(size - 2 * module) * EYE_RING_ROUNDING,
        fill=CREAM,
    )
    draw.rounded_rectangle(
        [x + 2 * module, y + 2 * module, x + size - 2 * module, y + size - 2 * module],
        radius=FINDER_CENTER_MODULES * module * EYE_CENTER_ROUNDING,
        fill=NAVY,
    )


def _place_logo(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    logo_path: Path,
    grid: _Grid,
    logo_start: int,
    logo_modules: int,
) -> None:
    """Fit the logo inside the clear zone, on a rounded cream card."""
    logo = load_logo(logo_path)
    zone = logo_modules * grid.module
    zone_xy = grid.origin + logo_start * grid.module
    card_pad = grid.module * CARD_PAD_MODULES
    draw.rounded_rectangle(
        [
            zone_xy + card_pad * CARD_INSET,
            zone_xy + card_pad * CARD_INSET,
            zone_xy + zone - card_pad * CARD_INSET,
            zone_xy + zone - card_pad * CARD_INSET,
        ],
        radius=zone * CARD_ROUNDING,
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
            if red > NEAR_WHITE and green > NEAR_WHITE and blue > NEAR_WHITE:
                pixels[x, y] = (red, green, blue, 0)

    position = (int(zone_xy + (zone - width) / 2), int(zone_xy + (zone - height) / 2))
    img.paste(logo, position, logo)