"""CloudFront KeyValueStore implementation of the redirect store.

Each write needs the store's current ETag, and fails with a conflict if another write got
in first, so conflicts are retried with a fresh ETag. Requests are signed with SigV4A,
which boto3 does through the awscrt package. See docs/DESIGN.md, section 5.
"""

from __future__ import annotations

import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from cases.ports import RedirectStore
from common.logger import elapsed_ms, logger

#: How many times to fetch a fresh ETag and try again after a conflict.
MAX_CONFLICT_RETRIES = 3


class KvsRedirectStore(RedirectStore):
    """Publishes redirects to a KeyValueStore.

    Parameters
    ----------
    client : Any
        A boto3 cloudfront-keyvaluestore client.
    kvs_arn : str
        The store's ARN.
    """

    def __init__(self, client: Any, kvs_arn: str) -> None:
        self._client = client
        self._kvs_arn = kvs_arn

    def publish(self, code: str, final_url: str) -> None:
        """Make a code redirect to a URL.

        Parameters
        ----------
        code : str
            The code name.
        final_url : str
            Where the code should redirect.

        Raises
        ------
        ConnectionError
            If the store cannot be updated after retrying.
        """
        start = time.monotonic()

        for attempt in range(1, MAX_CONFLICT_RETRIES + 1):
            try:
                etag = self._client.describe_key_value_store(KvsARN=self._kvs_arn)["ETag"]
                self._client.put_key(KvsARN=self._kvs_arn, Key=code, Value=final_url, IfMatch=etag)
            except ClientError as error:
                if error.response["Error"]["Code"] != "ConflictException":
                    raise ConnectionError("KVS write failed") from error
                logger.info("KVS conflict code=%s attempt=%s", code, attempt)
                continue
            except BotoCoreError as error:
                raise ConnectionError("KVS write failed") from error

            logger.info("KVS put code=%s status=ok latency_ms=%s", code, elapsed_ms(start))
            return

        raise ConnectionError("KVS write kept conflicting")