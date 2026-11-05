"""Shared structured logger.

One configured Powertools logger, which writes JSON log lines built on the standard
logging module. Handlers attach the correlation id at the entry point. The raw event is
never logged, because it carries the JWT.
"""

from __future__ import annotations

import os
import time

from aws_lambda_powertools import Logger

#: Tags every log line. The default gives a meaningful name locally and in tests.
SERVICE_NAME = os.environ.get("POWERTOOLS_SERVICE_NAME", "qr-manager")

logger = Logger(service=SERVICE_NAME)


def elapsed_ms(start: float) -> float:
    """Return the milliseconds since ``start``, for ``duration_ms`` in exit logs.

    Parameters
    ----------
    start : float
        A ``time.monotonic()`` reading taken at handler entry. Monotonic, so a clock
        adjustment during a request cannot produce a negative duration.

    Returns
    -------
    float
        Milliseconds elapsed, rounded to one decimal place.

    Examples
    --------
    >>> start = time.monotonic()
    >>> logger.info("Request end duration_ms=%s", elapsed_ms(start))
    """
    return round((time.monotonic() - start) * 1000, 1)