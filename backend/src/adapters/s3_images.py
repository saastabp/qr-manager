"""S3 implementation of the image store.

The bucket is private, so clients download through short-lived presigned URLs. See
docs/DESIGN.md, section 5.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from cases.ports import ImageStore

#: How long a download link works, in seconds.
DOWNLOAD_URL_SECONDS = 300


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
        """
        try:
            self._client.head_object(Bucket=self._bucket, Key=image_key(code, logo))
        except ClientError as error:
            if error.response["Error"]["Code"] == "404":
                return False
            raise

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