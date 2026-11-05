"""Render Lambda entry point: create one PNG and store it in S3.

Does nothing else. It receives everything it needs, and has no access to DynamoDB or any
business rules. Rendering the same request twice writes the same image to the same key,
so retries are safe. See docs/DESIGN.md, section 5.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import boto3
from aws_lambda_powertools import Logger

from png_renderer import render_png

ASSETS = Path(__file__).parent / "assets"
LOGO_FILES = {"full": "logo-full.png", "flower": "logo-flower.png"}

logger = Logger(service=os.environ.get("POWERTOOLS_SERVICE_NAME", "qr-manager-render"))
s3 = boto3.client("s3")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Render a PNG and store it.

    Parameters
    ----------
    event : dict[str, Any]
        Holds ``encoded_url``, ``logo``, ``key``, and ``correlation_id``.
    context : Any
        The Lambda invocation context. Unused.

    Returns
    -------
    dict[str, Any]
        The stored key and its size in bytes.

    Raises
    ------
    KeyError
        If the event is missing a field or names an unknown logo.
    botocore.exceptions.ClientError
        If the PNG cannot be stored.
    """
    start = time.monotonic()
    logger.set_correlation_id(event.get("correlation_id"))
    logger.info("Render start logo=%s key=%s", event.get("logo"), event.get("key"))

    try:
        logo_path = ASSETS / LOGO_FILES[event["logo"]]
        image = render_png(event["encoded_url"], logo_path)
        s3.put_object(
            Bucket=os.environ["IMAGES_BUCKET"],
            Key=event["key"],
            Body=image,
            ContentType="image/png",
        )
    except Exception:
        logger.exception("Render failed duration_ms=%s", _elapsed_ms(start))
        raise

    logger.info(
        "Render end status=ok key=%s bytes=%s duration_ms=%s",
        event["key"],
        len(image),
        _elapsed_ms(start),
    )
    return {"key": event["key"], "bytes": len(image)}


def _elapsed_ms(start: float) -> float:
    """Return the milliseconds since a monotonic reading."""
    return round((time.monotonic() - start) * 1000, 1)