"""Test setup: keeps tests away from real AWS credentials.

render_handler creates its S3 client on import, which looks for credentials straight away.
These fake values are set first, so tests never use a real profile.
"""

from __future__ import annotations

import os

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ.pop("AWS_PROFILE", None)