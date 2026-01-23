"""
Slack Events Lambda Handler.

Handles @mentions and DMs to the Cost Guardian bot.
Receives requests via Lambda Function URL.
"""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import UTC, datetime
from typing import Any

import boto3

from slack_aws_cost_guardian.config.loader import load_config, load_guardian_context
from slack_aws_cost_guardian.llm.client import LLMClient
from slack_aws_cost_guardian.llm.tools.cost_tools import create_cost_tools
from slack_aws_cost_guardian.llm.tools.schemas import COST_QUERY_SYSTEM_PROMPT, COST_TOOLS
from slack_aws_cost_guardian.notifications.slack.bot import SlackBotClient
from slack_aws_cost_guardian.notifications.slack.callback import verify_slack_signature


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda handler for Slack Events API.

    Handles:
    - URL verification challenges (during Slack app setup)
    - app_mention events (@guardian in channels)
    - message.im events (DMs to the bot)

    Environment variables:
    - TABLE_NAME: DynamoDB table name
    - CONFIG_SECRET_NAME: Secrets Manager secret with signing_secret, bot_token, and LLM API keys
    - CONFIG_BUCKET: S3 bucket with guardian-context.md
    - CONFIG_ENV: Deployment environment (dev/staging/prod)

    Args:
        event: Lambda Function URL event.
        context: Lambda context.

    Returns:
        HTTP response dict with statusCode and body.
    """
    print(f"Slack events handler invoked at {datetime.now(UTC).isoformat()}")

    # Extract headers and body
    headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
    body = event.get("body", "")

    # Handle base64 encoding
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    # Parse the body as JSON
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON body: {e}")
        return _error_response(400, "Invalid JSON")

    # Handle URL verification challenge (one-time during Slack app setup)
    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge", "")
        print("URL verification challenge received")
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": challenge,
        }

    # For all other events, verify signature
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")

    if not timestamp or not signature:
        print("Missing Slack signature headers")
        return _error_response(401, "Missing signature headers")

    slack_secret = _get_slack_secret()
    if not slack_secret:
        print("Could not retrieve Slack secret")
        return _error_response(500, "Configuration error")

    signing_secret = slack_secret.get("signing_secret")
    if not signing_secret:
        print("signing_secret not found in secret")
        return _error_response(500, "Configuration error")

    if not verify_slack_signature(signing_secret, timestamp, body, signature):
        print("Invalid Slack signature")
        return _error_response(401, "Invalid signature")

    # Handle event callbacks
    if payload.get("type") == "event_callback":
        event_data = payload.get("event", {})
        event_type = event_data.get("type")
        event_id = payload.get("event_id", "")

        # Deduplication: Check if we've already processed this event
        # Slack retries if we don't respond within 3 seconds
        if event_id and _is_duplicate_event(event_id):
            print(f"Duplicate event {event_id}, skipping")
            return _success_response()

        # Mark event as being processed
        if event_id:
            _mark_event_processed(event_id)

        # Ignore bot messages to avoid loops
        if event_data.get("bot_id"):
            print("Ignoring bot message")
            return _success_response()

        # Ignore message_changed, message_deleted, etc.
        if event_data.get("subtype"):
            print(f"Ignoring message subtype: {event_data.get('subtype')}")
            return _success_response()

        if event_type == "app_mention":
            return _handle_app_mention(event_data, slack_secret)
        elif event_type == "message" and event_data.get("channel_type") == "im":
            return _handle_direct_message(event_data, slack_secret)
        else:
            print(f"Unhandled event type: {event_type}")
            return _success_response()

    print(f"Unhandled payload type: {payload.get('type')}")
    return _success_response()


def _handle_app_mention(
    event_data: dict[str, Any],
    slack_secret: dict[str, str],
) -> dict[str, Any]:
    """Handle an @mention of the bot in a channel."""
    channel = event_data.get("channel", "")
    user = event_data.get("user", "")
    text = event_data.get("text", "")
    thread_ts = event_data.get("thread_ts") or event_data.get("ts")

    print(f"App mention from user {user} in channel {channel}")
    print(f"Message: {text}")

    # Extract the question (remove the @mention)
    question = _extract_question(text)

    if not question:
        # Just acknowledged the mention without a question
        bot_token = slack_secret.get("bot_token")
        if bot_token:
            bot = SlackBotClient(bot_token)
            bot.send_message(
                channel=channel,
                text="Hi! Ask me about your AWS costs. For example:\n"
                     "• What did we spend yesterday?\n"
                     "• Show me EC2 costs for the last 7 days\n"
                     "• What are our top services by cost?",
                thread_ts=thread_ts,
            )
        return _success_response()

    # Answer the question
    return _answer_question(
        question=question,
        channel=channel,
        thread_ts=thread_ts,
        slack_secret=slack_secret,
    )


def _handle_direct_message(
    event_data: dict[str, Any],
    slack_secret: dict[str, str],
) -> dict[str, Any]:
    """Handle a direct message to the bot."""
    channel = event_data.get("channel", "")
    user = event_data.get("user", "")
    text = event_data.get("text", "")

    print(f"DM from user {user}: {text}")

    if not text.strip():
        return _success_response()

    # In DMs, the full text is the question (no @mention to strip)
    return _answer_question(
        question=text,
        channel=channel,
        thread_ts=None,  # DMs don't use threads
        slack_secret=slack_secret,
    )


def _answer_question(
    question: str,
    channel: str,
    thread_ts: str | None,
    slack_secret: dict[str, str],
) -> dict[str, Any]:
    """
    Answer a cost question using the LLM with tools.

    Args:
        question: User's question.
        channel: Slack channel/DM to respond in.
        thread_ts: Thread timestamp for replies (None for DMs).
        slack_secret: Slack secret with bot_token.

    Returns:
        Success response to Slack.
    """
    bot_token = slack_secret.get("bot_token")
    if not bot_token:
        print("bot_token not found in Slack secret")
        return _error_response(500, "Bot token not configured")

    bot = SlackBotClient(bot_token)

    # Add a thinking reaction
    message_ts = thread_ts or ""
    if message_ts:
        bot.add_reaction(channel, message_ts, "hourglass_flowing_sand")

    try:
        # Load configuration
        config_bucket = os.environ.get("CONFIG_BUCKET", "")
        table_name = os.environ.get("TABLE_NAME", "")
        config_secret_name = os.environ.get("CONFIG_SECRET_NAME", "")
        region = os.environ.get("AWS_REGION", "us-east-1")

        # Load user context
        guardian_context = None
        if config_bucket:
            guardian_context = load_guardian_context(config_bucket, "config/guardian-context.md")

        # Initialize LLM client
        config = load_config("config/config.yaml")
        llm_client = LLMClient(
            config=config.llm,
            secret_name=config_secret_name,
            region=region,
        )

        # Create tool registry
        tool_registry = create_cost_tools(
            table_name=table_name if table_name else None,
            region=region,
        )

        # Get answer from LLM
        answer = llm_client.answer_cost_question(
            question=question,
            user_context=guardian_context,
            tool_registry=tool_registry,
            tools=COST_TOOLS,
            system_prompt=COST_QUERY_SYSTEM_PROMPT,
        )

        if answer:
            bot.send_message(
                channel=channel,
                text=answer,
                thread_ts=thread_ts,
            )
        else:
            bot.send_message(
                channel=channel,
                text="I'm sorry, I couldn't process your question. Please try again or rephrase your question.",
                thread_ts=thread_ts,
            )

    except Exception as e:
        print(f"Error answering question: {e}")
        bot.send_message(
            channel=channel,
            text=f"I encountered an error while processing your question: {e}",
            thread_ts=thread_ts,
        )

    return _success_response()


def _extract_question(text: str) -> str:
    """
    Extract the question from a message, removing the @mention.

    Args:
        text: Full message text (e.g., "<@U12345> what did we spend?").

    Returns:
        The question without the @mention.
    """
    # Remove @mentions (format: <@U12345> or <@U12345|username>)
    question = re.sub(r"<@[A-Z0-9]+(\|[^>]+)?>", "", text)
    return question.strip()


def _get_slack_secret() -> dict[str, str] | None:
    """Retrieve config secret from Secrets Manager."""
    secret_name = os.environ.get("CONFIG_SECRET_NAME")
    if not secret_name:
        return None

    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_name)
        return json.loads(response["SecretString"])
    except Exception as e:
        print(f"Error retrieving Slack secret: {e}")
        return None


# In-memory cache for deduplication (works within same Lambda instance)
# For cross-instance deduplication, we use DynamoDB
_processed_events: dict[str, float] = {}
_DEDUP_TTL_SECONDS = 300  # 5 minutes


def _is_duplicate_event(event_id: str) -> bool:
    """
    Check if this event has already been processed.

    Uses both in-memory cache (fast, same instance) and DynamoDB (cross-instance).
    """
    now = datetime.now(UTC).timestamp()

    # Check in-memory cache first (fast path)
    if event_id in _processed_events:
        if now - _processed_events[event_id] < _DEDUP_TTL_SECONDS:
            return True
        # Expired, remove from cache
        del _processed_events[event_id]

    # Check DynamoDB for cross-instance deduplication
    table_name = os.environ.get("TABLE_NAME")
    if not table_name:
        return False

    try:
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)

        # Try to get the event record
        response = table.get_item(
            Key={"PK": f"EVENT#{event_id}", "SK": "PROCESSED"},
        )

        if "Item" in response:
            # Event was already processed
            return True

    except Exception as e:
        print(f"Deduplication check failed: {e}")
        # On error, allow processing (better to duplicate than miss)

    return False


def _mark_event_processed(event_id: str) -> None:
    """Mark an event as processed in both memory and DynamoDB."""
    now = datetime.now(UTC)

    # Add to in-memory cache
    _processed_events[event_id] = now.timestamp()

    # Clean up old entries from memory cache
    cutoff = now.timestamp() - _DEDUP_TTL_SECONDS
    expired = [k for k, v in _processed_events.items() if v < cutoff]
    for k in expired:
        del _processed_events[k]

    # Store in DynamoDB with TTL
    table_name = os.environ.get("TABLE_NAME")
    if not table_name:
        return

    try:
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)

        table.put_item(
            Item={
                "PK": f"EVENT#{event_id}",
                "SK": "PROCESSED",
                "timestamp": now.isoformat(),
                "ttl": int(now.timestamp()) + _DEDUP_TTL_SECONDS,
            }
        )

    except Exception as e:
        print(f"Failed to mark event as processed: {e}")


def _success_response() -> dict[str, Any]:
    """Return a success response to Slack."""
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"ok": True}),
    }


def _error_response(status_code: int, message: str) -> dict[str, Any]:
    """Build an error response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }