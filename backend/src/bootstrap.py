"""Wires the AWS adapters into the cases.

The only place that knows which adapter fills each port. Settings come from environment
variables set by the Api stack. See docs/DESIGN.md, section 6.
"""

from __future__ import annotations

import os

import boto3
from botocore.config import Config

from adapters.dynamodb_codes import DynamoCodeRepository
from adapters.kvs_publisher import KvsRedirectStore
from adapters.render_invoker import LambdaRenderer
from adapters.s3_images import S3ImageStore
from cases.completion import Dependencies

#: Just over the render Lambda's 10 second timeout. Automatic retries are off, because a
#: slow render would otherwise be invoked a second time while the first is still running.
RENDER_CLIENT_CONFIG = Config(connect_timeout=2, read_timeout=12, retries={"max_attempts": 0})

#: Presigned URLs need signature version 4.
S3_CLIENT_CONFIG = Config(signature_version="s3v4")


def build_dependencies() -> Dependencies:
    """Create the real adapters from the environment.

    Returns
    -------
    Dependencies
        The ports and settings for the cases.

    Raises
    ------
    KeyError
        If a required environment variable is missing.
    """
    table = boto3.resource("dynamodb").Table(os.environ["CODES_TABLE"])

    return Dependencies(
        repository=DynamoCodeRepository(table),
        redirects=KvsRedirectStore(
            boto3.client("cloudfront-keyvaluestore"), os.environ["KVS_ARN"]
        ),
        images=S3ImageStore(
            boto3.client("s3", config=S3_CLIENT_CONFIG), os.environ["IMAGES_BUCKET"]
        ),
        renderer=LambdaRenderer(
            boto3.client("lambda", config=RENDER_CLIENT_CONFIG),
            os.environ["RENDER_FUNCTION"],
        ),
        redirect_domain=os.environ["REDIRECT_DOMAIN"],
    )