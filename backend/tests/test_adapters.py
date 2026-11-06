"""Tests for the AWS adapters."""

from __future__ import annotations

import io
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from moto import mock_aws

from adapters.dynamodb_codes import OWNER_INDEX, DynamoCodeRepository
from adapters.kvs_publisher import KvsRedirectStore
from adapters.render_invoker import LambdaRenderer
from adapters.s3_images import S3ImageStore, image_key
from bootstrap import RENDER_CLIENT_CONFIG, S3_CLIENT_CONFIG
from cases.get_image import DOWNLOAD_URL_SECONDS
from cases.policies import MAX_FINAL_URL_BYTES
from common.logger import logger
from models.qr_code import CodeKind, QrCode

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
INFRA = Path(__file__).parents[2] / "infra/lib"


def make_code(name: str = "bldc-flyer", **changes) -> QrCode:
    """Build a code with sensible defaults."""
    values = {
        "code": name,
        "kind": CodeKind.DYNAMIC,
        "destination": "https://example.com/a",
        "final_url": "https://example.com/a",
        "owner_sub": "owner-sub",
        "created_by": "owner@example.com",
        "created_at": NOW,
        "updated_by": "owner@example.com",
        "updated_at": NOW,
    }
    return QrCode(**{**values, **changes})


def client_error(code: str) -> ClientError:
    """Build the error boto3 raises for an AWS error code."""
    return ClientError({"Error": {"Code": code, "Message": code}}, "Operation")


@pytest.fixture
def repository():
    """Provide a repository on a fake table shaped like the real one."""
    with mock_aws():
        table = boto3.resource("dynamodb").create_table(
            TableName="codes",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "code", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "code", "AttributeType": "S"},
                {"AttributeName": "owner_sub", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "by-owner",
                    "KeySchema": [
                        {"AttributeName": "owner_sub", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        yield DynamoCodeRepository(table)


class TestDynamoRepository:
    """The storage mapping and conditional writes."""

    def test_a_code_survives_a_round_trip(self, repository):
        code = make_code(label="Flyer", campaign="bldc", version=3, synced=True)

        repository.add(code)

        assert repository.get("bldc-flyer") == code

    def test_a_name_can_only_be_claimed_once(self, repository):
        assert repository.add(make_code()) is True
        assert repository.add(make_code(label="Other")) is False
        assert repository.get("bldc-flyer").label == ""

    def test_a_missing_code_is_none(self, repository):
        assert repository.get("nothing") is None

    def test_list_returns_only_the_owners_codes_newest_first(self, repository):
        repository.add(make_code("older"))
        repository.add(make_code("newer", created_at=NOW + timedelta(hours=1)))
        repository.add(make_code("theirs", owner_sub="other-sub"))

        codes = repository.list_by_owner("owner-sub")

        assert [code.code for code in codes] == ["newer", "older"]

    def test_update_replaces_the_code(self, repository):
        repository.add(make_code())

        stored = repository.update(make_code(destination="https://example.com/b", version=2))

        assert stored is True
        assert repository.get("bldc-flyer").version == 2

    def test_update_refuses_a_change_based_on_a_stale_read(self, repository):
        repository.add(make_code(label="Newer", version=2))

        assert repository.update(make_code(label="Stale", version=2)) is False
        assert repository.get("bldc-flyer").label == "Newer"

    def test_mark_complete_records_the_steps(self, repository):
        repository.add(make_code())

        assert repository.mark_complete("bldc-flyer", 1, synced=True, rendered=True) is True

        stored = repository.get("bldc-flyer")
        assert stored.synced is True
        assert stored.rendered is True

    def test_mark_complete_refuses_a_stale_version(self, repository):
        repository.add(make_code(version=2))

        assert repository.mark_complete("bldc-flyer", 1, synced=True, rendered=True) is False
        assert repository.get("bldc-flyer").synced is False


@pytest.fixture
def logged(monkeypatch) -> list[str]:
    """Capture the text of info and warning log lines."""
    lines: list[str] = []

    def record(message: str, *args, **_kwargs) -> None:
        lines.append(message % args)

    monkeypatch.setattr(logger, "info", record)
    monkeypatch.setattr(logger, "warning", record)
    return lines


class FakeKvsClient:
    """Acts like the KVS client, raising the queued errors first."""

    def __init__(self, errors: list[Exception] | None = None) -> None:
        self.errors = errors or []
        self.etags = 0
        self.puts: list[dict] = []

    def describe_key_value_store(self, KvsARN: str) -> dict:  # noqa: N803
        """Return a new ETag on every call."""
        self.etags += 1
        return {"ETag": f"etag-{self.etags}"}

    def put_key(self, **kwargs) -> None:
        """Raise the next queued error, or record the write."""
        if self.errors:
            raise self.errors.pop(0)

        self.puts.append(kwargs)


class TestKvsRedirectStore:
    """Publishing to the key value store."""

    def test_publish_writes_with_the_current_etag(self):
        client = FakeKvsClient()

        KvsRedirectStore(client, "arn").publish("bldc-flyer", "https://example.com", "bldc")

        assert client.puts == [
            {
                "KvsARN": "arn",
                "Key": "bldc-flyer",
                "Value": '{"url":"https://example.com","campaign":"bldc"}',
                "IfMatch": "etag-1",
            }
        ]

    def test_a_blank_campaign_is_stored_blank(self):
        client = FakeKvsClient()

        KvsRedirectStore(client, "arn").publish("one-off", "https://example.com", "")

        assert client.puts[0]["Value"] == '{"url":"https://example.com","campaign":""}'

    def test_the_largest_allowed_value_fits_in_kvs(self):
        client = FakeKvsClient()
        prefix = "https://example.com/"
        longest_url = f"{prefix}{'a' * (MAX_FINAL_URL_BYTES - len(prefix))}"

        KvsRedirectStore(client, "arn").publish("c", longest_url, "b" * 64)

        assert len(client.puts[0]["Value"].encode()) <= 1024

    def test_a_conflict_retries_with_a_fresh_etag(self):
        client = FakeKvsClient([client_error("ConflictException")])

        KvsRedirectStore(client, "arn").publish("bldc-flyer", "https://example.com", "bldc")

        assert client.puts[0]["IfMatch"] == "etag-2"

    def test_repeated_conflicts_give_up(self):
        client = FakeKvsClient([client_error("ConflictException")] * 3)

        with pytest.raises(ConnectionError):
            KvsRedirectStore(client, "arn").publish("bldc-flyer", "https://example.com", "bldc")

    def test_access_denied_is_not_retried(self):
        client = FakeKvsClient([client_error("AccessDeniedException")])

        with pytest.raises(ConnectionError):
            KvsRedirectStore(client, "arn").publish("bldc-flyer", "https://example.com", "bldc")

        assert client.etags == 1

    def test_a_network_failure_is_reported(self):
        client = FakeKvsClient([EndpointConnectionError(endpoint_url="https://kvs")])

        with pytest.raises(ConnectionError):
            KvsRedirectStore(client, "arn").publish("bldc-flyer", "https://example.com", "bldc")

    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (client_error("AccessDeniedException"), "AccessDeniedException"),
            (EndpointConnectionError(endpoint_url="https://kvs"), "EndpointConnectionError"),
        ],
    )
    def test_a_failed_write_logs_its_status_and_latency(self, logged, error, status):
        client = FakeKvsClient([error])

        with pytest.raises(ConnectionError):
            KvsRedirectStore(client, "arn").publish("bldc-flyer", "https://example.com", "bldc")

        assert logged[-1].startswith(f"KVS put code=bldc-flyer status={status} latency_ms=")

    def test_giving_up_after_conflicts_logs_why(self, logged):
        client = FakeKvsClient([client_error("ConflictException")] * 3)

        with pytest.raises(ConnectionError):
            KvsRedirectStore(client, "arn").publish("bldc-flyer", "https://example.com", "bldc")

        assert "status=kept_conflicting" in logged[-1]


class FakeS3Client:
    """Raises a chosen error from head_object."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def head_object(self, **kwargs) -> None:
        """Raise the chosen error, if any."""
        if self.error:
            raise self.error


class TestS3ImageStore:
    """Finding PNGs and creating download links."""

    def test_an_existing_png_is_found(self):
        assert S3ImageStore(FakeS3Client(), "images").exists("bldc-flyer", "full") is True

    def test_a_404_means_no_png(self):
        store = S3ImageStore(FakeS3Client(client_error("404")), "images")

        assert store.exists("bldc-flyer", "full") is False

    @pytest.mark.parametrize(("error", "status"), [(None, "ok"), (client_error("404"), "404")])
    def test_a_check_logs_its_status_and_latency(self, logged, error, status):
        S3ImageStore(FakeS3Client(error), "images").exists("bldc-flyer", "full")

        assert logged[-1].startswith(
            f"S3 head key=qr-images/bldc-flyer/full.png status={status} latency_ms="
        )

    def test_a_403_is_a_real_error(self):
        store = S3ImageStore(FakeS3Client(client_error("403")), "images")

        with pytest.raises(ClientError):
            store.exists("bldc-flyer", "full")

    def test_download_url_points_at_the_png_and_expires(self):
        store = S3ImageStore(boto3.client("s3", config=S3_CLIENT_CONFIG), "images")

        url = store.download_url("bldc-flyer", "full")

        assert "qr-images/bldc-flyer/full.png" in url
        assert "bldc-flyer-full.png" in url
        assert f"X-Amz-Expires={DOWNLOAD_URL_SECONDS}" in url


class FakeLambdaClient:
    """Returns a chosen response, or raises a chosen error."""

    def __init__(self, response: dict | None = None, error: Exception | None = None) -> None:
        self.response = response or {"StatusCode": 200}
        self.error = error
        self.calls: list[dict] = []

    def invoke(self, **kwargs) -> dict:
        """Record the call."""
        self.calls.append(kwargs)
        if self.error:
            raise self.error

        return self.response


class TestLambdaRenderer:
    """Invoking the render Lambda."""

    def test_render_sends_everything_the_lambda_needs(self):
        client = FakeLambdaClient()

        LambdaRenderer(client, "render").render("bldc-flyer", "https://qr/bldc-flyer", "full")

        call = client.calls[0]
        payload = json.loads(call["Payload"])
        assert call["FunctionName"] == "render"
        assert call["InvocationType"] == "RequestResponse"
        assert payload["encoded_url"] == "https://qr/bldc-flyer"
        assert payload["logo"] == "full"
        assert payload["key"] == image_key("bldc-flyer", "full")

    def test_a_crash_in_the_lambda_is_reported(self):
        response = {"FunctionError": "Unhandled", "Payload": io.BytesIO(b'{"errorType":"X"}')}

        with pytest.raises(ConnectionError):
            LambdaRenderer(FakeLambdaClient(response), "render").render("c", "u", "full")

    def test_a_failed_invoke_is_reported(self):
        client = FakeLambdaClient(error=client_error("ServiceException"))

        with pytest.raises(ConnectionError):
            LambdaRenderer(client, "render").render("c", "u", "full")

    def test_a_crash_logs_its_status_and_latency(self, logged):
        response = {"FunctionError": "Unhandled", "Payload": io.BytesIO(b"{}")}

        with pytest.raises(ConnectionError):
            LambdaRenderer(FakeLambdaClient(response), "render").render("c", "u", "full")

        assert logged[-1].startswith("Render invoked code=c logo=full status=Unhandled latency_ms=")

    def test_a_failed_invoke_logs_its_status_and_latency(self, logged):
        client = FakeLambdaClient(error=client_error("ServiceException"))

        with pytest.raises(ConnectionError):
            LambdaRenderer(client, "render").render("c", "u", "full")

        assert "status=ClientError" in logged[-1]


class TestInfraAgreement:
    """Values that exist in Python and in CDK, so the two must not drift apart."""

    def test_the_owner_index_exists_on_the_table(self):
        source = (INFRA / "api/code-table.ts").read_text()

        assert f"indexName: '{OWNER_INDEX}'" in source

    def test_the_read_timeout_outlasts_the_render_lambda(self):
        source = (INFRA / "api/render-service.ts").read_text()
        timeout = re.search(r"timeout: Duration\.seconds\((\d+)\)", source)

        assert timeout is not None
        assert RENDER_CLIENT_CONFIG.read_timeout > int(timeout.group(1))