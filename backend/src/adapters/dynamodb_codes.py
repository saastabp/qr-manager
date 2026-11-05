"""DynamoDB implementation of the code repository.

The only place that knows the storage shape of a code. Conditional writes enforce the
rules that must be atomic: a name is claimed once, and finished steps are recorded only if
nothing has changed since. See docs/DESIGN.md, section 4.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from cases.ports import CodeRepository
from common.logger import logger
from models.qr_code import CodeKind, QrCode

OWNER_INDEX = "by-owner"


class DynamoCodeRepository(CodeRepository):
    """Stores codes in a DynamoDB table.

    Parameters
    ----------
    table : Any
        A boto3 DynamoDB Table resource.
    """

    def __init__(self, table: Any) -> None:
        self._table = table

    def add(self, code: QrCode) -> bool:
        """Store a new code, refusing a name that exists.

        Parameters
        ----------
        code : QrCode
            The code to store.

        Returns
        -------
        bool
            True if stored, False if the name already exists.
        """
        try:
            self._table.put_item(
                Item=_to_item(code),
                ConditionExpression="attribute_not_exists(#code)",
                ExpressionAttributeNames={"#code": "code"},
            )
        except ClientError as error:
            if _is_condition_failure(error):
                return False
            raise

        logger.info("DynamoDB put code=%s version=%s", code.code, code.version)
        return True

    def get(self, code: str) -> QrCode | None:
        """Read the latest value of a code.

        Parameters
        ----------
        code : str
            The code name.

        Returns
        -------
        QrCode or None
            The stored code, or None if it does not exist.
        """
        response = self._table.get_item(Key={"code": code}, ConsistentRead=True)
        item = response.get("Item")
        if item is None:
            return None

        return _from_item(item)

    def list_by_owner(self, owner_sub: str) -> list[QrCode]:
        """List one owner's codes, newest first.

        Parameters
        ----------
        owner_sub : str
            The owner's Cognito id.

        Returns
        -------
        list[QrCode]
            The owner's codes.
        """
        query: dict[str, Any] = {
            "IndexName": OWNER_INDEX,
            "KeyConditionExpression": Key("owner_sub").eq(owner_sub),
            "ScanIndexForward": False,
        }
        codes: list[QrCode] = []
        while True:
            response = self._table.query(**query)
            codes.extend(_from_item(item) for item in response["Items"])

            last_key = response.get("LastEvaluatedKey")
            if last_key is None:
                return codes
            query["ExclusiveStartKey"] = last_key

    def update(self, code: QrCode) -> None:
        """Replace a stored code.

        Parameters
        ----------
        code : QrCode
            The new value.
        """
        self._table.put_item(Item=_to_item(code))
        logger.info("DynamoDB update code=%s version=%s", code.code, code.version)

    def mark_complete(self, code: str, version: int, *, synced: bool, rendered: bool) -> bool:
        """Record finished steps, only if the version still matches.

        Parameters
        ----------
        code : str
            The code name.
        version : int
            The version these steps were completed for.
        synced : bool
            New value of the synced flag.
        rendered : bool
            New value of the rendered flag.

        Returns
        -------
        bool
            True if recorded, False if a newer version exists.
        """
        try:
            self._table.update_item(
                Key={"code": code},
                UpdateExpression="SET #synced = :synced, #rendered = :rendered",
                ConditionExpression="#version = :version",
                ExpressionAttributeNames={
                    "#synced": "synced",
                    "#rendered": "rendered",
                    "#version": "version",
                },
                ExpressionAttributeValues={
                    ":synced": synced,
                    ":rendered": rendered,
                    ":version": version,
                },
            )
        except ClientError as error:
            if _is_condition_failure(error):
                return False
            raise

        logger.info("DynamoDB mark complete code=%s version=%s", code, version)
        return True


def _is_condition_failure(error: ClientError) -> bool:
    """Tell a failed condition apart from a real DynamoDB error."""
    return error.response["Error"]["Code"] == "ConditionalCheckFailedException"


def _to_item(code: QrCode) -> dict[str, Any]:
    """Convert a code to its storage shape."""
    item = code.model_dump()
    item["kind"] = code.kind.value
    item["created_at"] = code.created_at.isoformat()
    item["updated_at"] = code.updated_at.isoformat()
    return item


def _from_item(item: dict[str, Any]) -> QrCode:
    """Convert a stored item to a code."""
    return QrCode(
        **{
            **item,
            "kind": CodeKind(item["kind"]),
            "created_at": datetime.fromisoformat(item["created_at"]),
            "updated_at": datetime.fromisoformat(item["updated_at"]),
            # DynamoDB returns numbers as Decimal.
            "version": int(item["version"]),
        }
    )