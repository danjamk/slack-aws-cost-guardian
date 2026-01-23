"""
Slack Callback Lambda Handler.

Handles interactive button clicks from Slack anomaly alerts.
Receives requests via Lambda Function URL.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from typing import Any

import boto3

from slack_aws_cost_guardian.notifications.slack.callback import (
    SlackInteraction,
    parse_interaction_payload,
    replace_actions_with_confirmation,
    send_response_url_update,
    verify_slack_signature,
)
from slack_aws_cost_guardian.storage.dynamodb import DynamoDBStorage
from slack_aws_cost_guardian.storage.models import AnomalyFeedback, FeedbackType


# Map Slack action IDs to feedback types
ACTION_TO_FEEDBACK: dict[str, FeedbackType] = {
    "feedback_expected": FeedbackType.EXPECTED,
    "feedback_unexpected": FeedbackType.UNEXPECTED,
    "feedback_investigating": FeedbackType.INVESTIGATING,
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda handler for Slack interactive callbacks.

    Receives requests via Function URL when users click buttons on anomaly alerts.

    Environment variables:
    - TABLE_NAME: DynamoDB table name
    - CONFIG_SECRET_NAME: Secrets Manager secret with signing_secret

    Args:
        event: Lambda Function URL event.
        context: Lambda context.

    Returns:
        HTTP response dict with statusCode and body.
    """
    print(f"Slack callback received at {datetime.now(UTC).isoformat()}")

    # Extract headers and body
    headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
    body = event.get("body", "")

    # Handle base64 encoding
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    # Get signature headers
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")

    if not timestamp or not signature:
        print("Missing Slack signature headers")
        return _error_response(401, "Missing signature headers")

    # Verify signature
    signing_secret = _get_signing_secret()
    if not signing_secret:
        print("Could not retrieve signing secret")
        return _error_response(500, "Configuration error")

    if not verify_slack_signature(signing_secret, timestamp, body, signature):
        print("Invalid Slack signature")
        return _error_response(401, "Invalid signature")

    # Parse the interaction payload
    try:
        interaction = parse_interaction_payload(body)
    except ValueError as e:
        print(f"Failed to parse payload: {e}")
        return _error_response(400, str(e))

    print(f"Received action: {interaction.action_id} for alert {interaction.alert_id}")
    print(f"User: {interaction.user_name} ({interaction.user_id})")

    # Map action to feedback type
    feedback_type = ACTION_TO_FEEDBACK.get(interaction.action_id)
    if not feedback_type:
        print(f"Unknown action_id: {interaction.action_id}")
        return _error_response(400, f"Unknown action: {interaction.action_id}")

    # Store feedback in DynamoDB
    try:
        _store_feedback(interaction, feedback_type)
        print(f"Stored feedback: {feedback_type.value}")
    except Exception as e:
        print(f"Failed to store feedback: {e}")
        # Continue to update Slack message even if storage fails

    # Update the Slack message to show confirmation
    try:
        _update_slack_message(interaction, feedback_type)
        print("Updated Slack message with confirmation")
    except Exception as e:
        print(f"Failed to update Slack message: {e}")
        # Don't fail the request - feedback was stored

    # Return success to Slack
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"ok": True}),
    }


def _get_signing_secret() -> str | None:
    """Retrieve Slack signing secret from Secrets Manager."""
    secret_name = os.environ.get("CONFIG_SECRET_NAME")
    if not secret_name:
        return None

    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_name)
        secret_data = json.loads(response["SecretString"])
        return secret_data.get("signing_secret")
    except Exception as e:
        print(f"Error retrieving signing secret: {e}")
        return None


def _store_feedback(interaction: SlackInteraction, feedback_type: FeedbackType) -> None:
    """Store feedback in DynamoDB."""
    table_name = os.environ.get("TABLE_NAME")
    if not table_name:
        raise ValueError("TABLE_NAME environment variable not set")

    storage = DynamoDBStorage(table_name)

    feedback = AnomalyFeedback(
        alert_id=interaction.alert_id,
        date=datetime.now(UTC).date().isoformat(),
        user_id=interaction.user_id,
        user_name=interaction.user_name,
        feedback_type=feedback_type,
        cost_impact=0.0,  # Could be enhanced to extract from original message
        affected_services=[],  # Could be enhanced to extract from original message
    )

    storage.put_feedback(feedback)


def _update_slack_message(
    interaction: SlackInteraction,
    feedback_type: FeedbackType,
) -> None:
    """Update the original Slack message with confirmation."""
    if not interaction.response_url:
        print("No response_url available, skipping message update")
        return

    if not interaction.original_blocks:
        print("No original blocks available, skipping message update")
        return

    # Replace action buttons with confirmation
    updated_blocks = replace_actions_with_confirmation(
        blocks=interaction.original_blocks,
        alert_id=interaction.alert_id,
        feedback_type=feedback_type.value,
        user_name=interaction.user_name,
    )

    # Send update to Slack
    send_response_url_update(
        response_url=interaction.response_url,
        blocks=updated_blocks,
        replace_original=True,
    )


def _error_response(status_code: int, message: str) -> dict[str, Any]:
    """Build an error response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }