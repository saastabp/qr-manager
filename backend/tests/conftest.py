"""Test setup: keeps tests away from real AWS credentials.

The adapter tests create boto3 clients, which look for credentials. These fake values are
set first, so tests can never reach a real account.
"""

from __future__ import annotations

import os

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ.pop("AWS_PROFILE", None)