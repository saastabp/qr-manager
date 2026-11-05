"""Tests for the render Lambda. Decodes real PNGs, proving the codes can be scanned."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
import zxingcpp
from PIL import Image

import render_handler
from png_renderer import render_png

ASSETS = Path(__file__).parents[1] / "src" / "assets"
SHORT_URL = "https://qr.360balancedliving.com/bldc-flyer"


def decode(image: Image.Image) -> str | None:
    """Return the text a scanner reads from an image, or None if it reads nothing."""
    results = zxingcpp.read_barcodes(image)
    if not results:
        return None

    return results[0].text


class FakeS3:
    """Records what would have been stored."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}

    def put_object(self, **kwargs) -> None:
        """Record an upload."""
        self.objects[kwargs["Key"]] = kwargs


@pytest.mark.parametrize("logo", ["logo-full.png", "logo-flower.png"])
@pytest.mark.parametrize("size", [2048, 1024, 512, 256, 120])
@pytest.mark.parametrize("mode", ["RGB", "L"])
def test_png_scans_at_every_size(logo, size, mode):
    image = Image.open(BytesIO(render_png(SHORT_URL, ASSETS / logo)))

    scaled = image.resize((size, size), Image.LANCZOS).convert(mode)

    assert decode(scaled) == SHORT_URL


def test_png_is_print_ready():
    image = Image.open(BytesIO(render_png(SHORT_URL, ASSETS / "logo-full.png")))

    assert image.size == (2048, 2048)
    assert round(image.info["dpi"][0]) == 300


def test_longest_allowed_url_scans():
    url = f"https://example.com/{'a' * 1000}"

    image = Image.open(BytesIO(render_png(url, ASSETS / "logo-full.png")))

    assert decode(image) == url


def test_handler_stores_the_png(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(render_handler, "s3", fake)
    monkeypatch.setenv("IMAGES_BUCKET", "images")
    event = {
        "encoded_url": SHORT_URL,
        "logo": "full",
        "key": "qr-images/bldc-flyer/full.png",
        "correlation_id": "request-1",
    }

    result = render_handler.lambda_handler(event, None)

    stored = fake.objects["qr-images/bldc-flyer/full.png"]
    assert stored["Bucket"] == "images"
    assert stored["ContentType"] == "image/png"
    assert result["bytes"] == len(stored["Body"])
    assert decode(Image.open(BytesIO(stored["Body"]))) == SHORT_URL


def test_handler_refuses_an_unknown_logo(monkeypatch):
    monkeypatch.setattr(render_handler, "s3", FakeS3())
    monkeypatch.setenv("IMAGES_BUCKET", "images")
    event = {"encoded_url": SHORT_URL, "logo": "giant", "key": "k", "correlation_id": "r"}

    with pytest.raises(KeyError):
        render_handler.lambda_handler(event, None)