"""Render Lambda implementation of the renderer.

Invokes the render Lambda and waits for the result. See docs/DESIGN.md, section 5.
"""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from adapters.s3_images import image_key
from cases.ports import Renderer
from common.logger import elapsed_ms, logger


class LambdaRenderer(Renderer):
    """Renders PNGs by invoking the render Lambda.

    Parameters
    ----------
    client : Any
        A boto3 Lambda client.
    function_name : str
        The render Lambda's name.
    """

    def __init__(self, client: Any, function_name: str) -> None:
        self._client = client
        self._function_name = function_name

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
            If the render Lambda cannot be reached or reports a failure.
        """
        start = time.monotonic()
        payload = {
            "encoded_url": encoded_url,
            "logo": logo,
            "key": image_key(code, logo),
            "correlation_id": logger.get_correlation_id(),
        }

        try:
            response = self._client.invoke(
                FunctionName=self._function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(payload),
            )
        except (BotoCoreError, ClientError) as error:
            _log_failure(code, logo, type(error).__name__, start)
            raise ConnectionError("Render Lambda could not be invoked") from error

        # A crash inside the function is reported here, not raised by invoke().
        if "FunctionError" in response:
            detail = response["Payload"].read().decode()
            _log_failure(code, logo, response["FunctionError"], start)
            raise ConnectionError(f"Render Lambda failed: {detail}")

        logger.info(
            "Render invoked code=%s logo=%s status=ok latency_ms=%s",
            code,
            logo,
            elapsed_ms(start),
        )


def _log_failure(code: str, logo: str, status: str, start: float) -> None:
    """Log a failed invoke with the same fields as a successful one."""
    logger.warning(
        "Render invoked code=%s logo=%s status=%s latency_ms=%s",
        code,
        logo,
        status,
        elapsed_ms(start),
    )