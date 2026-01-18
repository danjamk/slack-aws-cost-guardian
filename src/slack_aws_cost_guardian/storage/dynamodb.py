"""DynamoDB storage operations for Slack AWS Cost Guardian."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource

import boto3
from boto3.dynamodb.conditions import Key

from slack_aws_cost_guardian.storage.models import (
    AnomalyFeedback,
    ChangeLog,
    ChangeStatus,
    CostSnapshot,
)


class DynamoDBStorage:
    """DynamoDB storage client for cost monitoring data."""

    def __init__(
        self,
        table_name: str,
        dynamodb_resource: boto3.resource | None = None,
    ):
        """
        Initialize DynamoDB storage.

        Args:
            table_name: Name of the DynamoDB table.
            dynamodb_resource: Optional boto3 DynamoDB resource. If None, creates one.
        """
        self.dynamodb = dynamodb_resource or boto3.resource("dynamodb")
        self.table = self.dynamodb.Table(table_name)
        self.table_name = table_name

    # =========================================================================
    # Cost Snapshots
    # =========================================================================

    def put_snapshot(self, snapshot: CostSnapshot) -> None:
        """Store a cost snapshot."""
        self.table.put_item(Item=snapshot.to_dynamodb_item())

    def get_snapshot(self, date: str, hour: int, account_id: str) -> CostSnapshot | None:
        """
        Get a specific cost snapshot.

        Args:
            date: Date in YYYY-MM-DD format.
            hour: Hour (0-23).
            account_id: AWS account ID.

        Returns:
            CostSnapshot if found, None otherwise.
        """
        response = self.table.get_item(
            Key={
                "PK": f"SNAPSHOT#{date}",
                "SK": f"HOUR#{hour:02d}#{account_id}",
            }
        )
        if "Item" in response:
            return CostSnapshot.from_dynamodb_item(response["Item"])
        return None

    def get_snapshots_for_date(self, date: str) -> list[CostSnapshot]:
        """
        Get all snapshots for a specific date.

        Args:
            date: Date in YYYY-MM-DD format.

        Returns:
            List of CostSnapshot objects.
        """
        response = self.table.query(
            KeyConditionExpression=Key("PK").eq(f"SNAPSHOT#{date}")
        )
        return [CostSnapshot.from_dynamodb_item(item) for item in response.get("Items", [])]

    def get_recent_snapshots(
        self,
        days: int = 14,
        account_id: str | None = None,
    ) -> list[CostSnapshot]:
        """
        Get snapshots for the last N days.

        Args:
            days: Number of days to look back.
            account_id: Optional account ID filter.

        Returns:
            List of CostSnapshot objects, sorted by date descending.
        """
        snapshots = []
        today = datetime.utcnow().date()

        for i in range(days):
            date = (today - timedelta(days=i)).isoformat()
            date_snapshots = self.get_snapshots_for_date(date)

            if account_id:
                date_snapshots = [s for s in date_snapshots if s.account_id == account_id]

            snapshots.extend(date_snapshots)

        return snapshots

    def get_latest_snapshot(self, account_id: str) -> CostSnapshot | None:
        """
        Get the most recent snapshot for an account.

        Args:
            account_id: AWS account ID.

        Returns:
            Most recent CostSnapshot if found, None otherwise.
        """
        today = datetime.utcnow().date()

        # Check last 3 days to handle missing data
        for i in range(3):
            date = (today - timedelta(days=i)).isoformat()
            snapshots = self.get_snapshots_for_date(date)
            account_snapshots = [s for s in snapshots if s.account_id == account_id]

            if account_snapshots:
                # Return the one with the highest hour
                return max(account_snapshots, key=lambda s: s.hour)

        return None

    # =========================================================================
    # Anomaly Feedback
    # =========================================================================

    def put_feedback(self, feedback: AnomalyFeedback) -> None:
        """Store anomaly feedback."""
        self.table.put_item(Item=feedback.to_dynamodb_item())

    def get_feedback(self, date: str, alert_id: str) -> AnomalyFeedback | None:
        """
        Get feedback for a specific alert.

        Args:
            date: Date in YYYY-MM-DD format.
            alert_id: Alert ID.

        Returns:
            AnomalyFeedback if found, None otherwise.
        """
        response = self.table.get_item(
            Key={
                "PK": f"FEEDBACK#{date}",
                "SK": f"ALERT#{alert_id}",
            }
        )
        if "Item" in response:
            return AnomalyFeedback.from_dynamodb_item(response["Item"])
        return None

    def get_feedback_for_date(self, date: str) -> list[AnomalyFeedback]:
        """
        Get all feedback for a specific date.

        Args:
            date: Date in YYYY-MM-DD format.

        Returns:
            List of AnomalyFeedback objects.
        """
        response = self.table.query(
            KeyConditionExpression=Key("PK").eq(f"FEEDBACK#{date}")
        )
        return [AnomalyFeedback.from_dynamodb_item(item) for item in response.get("Items", [])]

    def get_recent_feedback(self, days: int = 30) -> list[AnomalyFeedback]:
        """
        Get feedback from the last N days.

        Args:
            days: Number of days to look back.

        Returns:
            List of AnomalyFeedback objects.
        """
        feedback_list = []
        today = datetime.utcnow().date()

        for i in range(days):
            date = (today - timedelta(days=i)).isoformat()
            feedback_list.extend(self.get_feedback_for_date(date))

        return feedback_list

    # =========================================================================
    # Change Log
    # =========================================================================

    def put_change(self, change: ChangeLog) -> None:
        """Store a change log entry."""
        self.table.put_item(Item=change.to_dynamodb_item())

    def get_changes_for_service(self, service: str) -> list[ChangeLog]:
        """
        Get all changes for a specific service.

        Args:
            service: AWS service name.

        Returns:
            List of ChangeLog objects.
        """
        response = self.table.query(
            KeyConditionExpression=Key("PK").eq(f"CHANGE#{service}")
        )
        return [ChangeLog.from_dynamodb_item(item) for item in response.get("Items", [])]

    def get_active_changes(self) -> list[ChangeLog]:
        """
        Get all active (unresolved) changes.

        Note: This requires a scan with filter, which is less efficient.
        For production with many changes, consider using a GSI on status.

        Returns:
            List of active ChangeLog objects.
        """
        # Scan for all CHANGE# items with active status
        # In production, use a GSI on status for better performance
        response = self.table.scan(
            FilterExpression="begins_with(PK, :pk) AND #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":pk": "CHANGE#",
                ":status": ChangeStatus.ACTIVE.value,
            },
        )

        changes = [ChangeLog.from_dynamodb_item(item) for item in response.get("Items", [])]

        # Handle pagination
        while "LastEvaluatedKey" in response:
            response = self.table.scan(
                FilterExpression="begins_with(PK, :pk) AND #status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pk": "CHANGE#",
                    ":status": ChangeStatus.ACTIVE.value,
                },
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            changes.extend(
                ChangeLog.from_dynamodb_item(item) for item in response.get("Items", [])
            )

        return changes

    def update_change_status(
        self,
        service: str,
        change_id: str,
        date: str,
        new_status: ChangeStatus,
        resolution_notes: str | None = None,
    ) -> None:
        """
        Update the status of a change log entry.

        Args:
            service: AWS service name.
            change_id: Change ID.
            date: Original date of the change.
            new_status: New status to set.
            resolution_notes: Optional notes about resolution.
        """
        update_expression = "SET #status = :status"
        expression_values: dict = {":status": new_status.value}
        expression_names = {"#status": "status"}

        if resolution_notes:
            update_expression += ", resolution_notes = :notes"
            expression_values[":notes"] = resolution_notes

        self.table.update_item(
            Key={
                "PK": f"CHANGE#{service}",
                "SK": f"DATE#{date}#{change_id}",
            },
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_names,
            ExpressionAttributeValues=expression_values,
        )

    # =========================================================================
    # Batch Operations
    # =========================================================================

    def batch_put_snapshots(self, snapshots: list[CostSnapshot]) -> None:
        """
        Store multiple snapshots in a batch.

        Args:
            snapshots: List of CostSnapshot objects to store.
        """
        with self.table.batch_writer() as batch:
            for snapshot in snapshots:
                batch.put_item(Item=snapshot.to_dynamodb_item())

    def iter_all_snapshots(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Iterator[CostSnapshot]:
        """
        Iterate over all snapshots, optionally filtered by date range.

        Args:
            start_date: Optional start date (inclusive) in YYYY-MM-DD format.
            end_date: Optional end date (inclusive) in YYYY-MM-DD format.

        Yields:
            CostSnapshot objects.
        """
        scan_kwargs: dict = {
            "FilterExpression": "begins_with(PK, :pk)",
            "ExpressionAttributeValues": {":pk": "SNAPSHOT#"},
        }

        if start_date or end_date:
            filter_parts = ["begins_with(PK, :pk)"]
            if start_date:
                filter_parts.append("#date >= :start")
                scan_kwargs.setdefault("ExpressionAttributeNames", {})["#date"] = "date"
                scan_kwargs["ExpressionAttributeValues"][":start"] = start_date
            if end_date:
                filter_parts.append("#date <= :end")
                scan_kwargs.setdefault("ExpressionAttributeNames", {})["#date"] = "date"
                scan_kwargs["ExpressionAttributeValues"][":end"] = end_date
            scan_kwargs["FilterExpression"] = " AND ".join(filter_parts)

        response = self.table.scan(**scan_kwargs)
        for item in response.get("Items", []):
            yield CostSnapshot.from_dynamodb_item(item)

        while "LastEvaluatedKey" in response:
            scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = self.table.scan(**scan_kwargs)
            for item in response.get("Items", []):
                yield CostSnapshot.from_dynamodb_item(item)