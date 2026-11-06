"""S3 implementation of the image store.

The bucket is private, so clients download through short-lived presigned URLs. See
docs/DESIGN.md, section 5.
"""

from __future__ import annotations

import time
from typing import Any

from botocore.exceptions import ClientError

from cases.get_image import DOWNLOAD_URL_SECONDS
from cases.ports import ImageStore
from common.logger import elapsed_ms, logger


def image_key(code: str, logo: str) -> str:
    """Build the S3 key for a PNG.

    Parameters
    ----------
    code : str
        The code name.
    logo : str
        The logo variant.

    Returns
    -------
    str
        The key, such as ``qr-images/bldc-flyer/full.png``.
    """
    return f"qr-images/{code}/{logo}.png"


class S3ImageStore(ImageStore):
    """Reads PNGs from an S3 bucket.

    Parameters
    ----------
    client : Any
        A boto3 S3 client.
    bucket : str
        The images bucket.
    """

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def exists(self, code: str, logo: str) -> bool:
        """Check whether a PNG is stored.

        Parameters
        ----------
        code : str
            The code name.
        logo : str
            The logo variant.

        Returns
        -------
        bool
            True if the PNG exists.

        Raises
        ------
        botocore.exceptions.ClientError
            If S3 fails for any reason other than the PNG being missing.
        """
        key = image_key(code, logo)
        start = time.monotonic()
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            status = error.response["Error"]["Code"]
            logger.info("S3 head key=%s status=%s latency_ms=%s", key, status, elapsed_ms(start))
            if status == "404":
                return False
            raise

        logger.info("S3 head key=%s status=ok latency_ms=%s", key, elapsed_ms(start))
        return True

    def download_url(self, code: str, logo: str) -> str:
        """Create a short-lived URL for downloading a PNG.

        Parameters
        ----------
        code : str
            The code name.
        logo : str
            The logo variant.

        Returns
        -------
        str
            The presigned URL.
        """
        return self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": image_key(code, logo),
                # Browsers save the file under a useful name, rather than displaying it.
                "ResponseContentDisposition": f'attachment; filename="{code}-{logo}.png"',
            },
            ExpiresIn=DOWNLOAD_URL_SECONDS,
        )