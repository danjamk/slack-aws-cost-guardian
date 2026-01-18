"""Slack interactive callback handling."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class SlackInteraction:
    """Parsed Slack interaction payload."""

    action_id: str
    alert_id: str
    block_id: str
    user_id: str
    user_name: str
    response_url: str
    channel_id: str
    original_blocks: list[dict[str, Any]]


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: str,
    signature: str,
) -> bool:
    """
    Verify Slack request signature using HMAC-SHA256.

    Args:
        signing_secret: Slack app signing secret.
        timestamp: X-Slack-Request-Timestamp header value.
        body: Raw request body (URL-encoded).
        signature: X-Slack-Signature header value.

    Returns:
        True if signature is valid, False otherwise.
    """
    # Reject requests older than 5 minutes (replay attack prevention)
    try:
        request_time = int(timestamp)
        if abs(time.time() - request_time) > 60 * 5:
            return False
    except (ValueError, TypeError):
        return False

    # Compute expected signature
    sig_basestring = f"v0:{timestamp}:{body}"
    expected_signature = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison
    return hmac.compare_digest(expected_signature, signature)


def parse_interaction_payload(body: str) -> SlackInteraction:
    """
    Parse Slack interaction payload from URL-encoded body.

    Args:
        body: URL-encoded request body containing payload={json}.

    Returns:
        SlackInteraction with extracted fields.

    Raises:
        ValueError: If payload is malformed or missing required fields.
    """
    # Parse URL-encoded body
    parsed = urllib.parse.parse_qs(body)
    payload_str = parsed.get("payload", [""])[0]

    if not payload_str:
        raise ValueError("Missing payload in request body")

    payload = json.loads(payload_str)

    # Validate payload type
    if payload.get("type") != "block_actions":
        raise ValueError(f"Unexpected payload type: {payload.get('type')}")

    # Extract action info
    actions = payload.get("actions", [])
    if not actions:
        raise ValueError("No actions in payload")

    action = actions[0]
    action_id = action.get("action_id", "")
    alert_id = action.get("value", "")
    block_id = action.get("block_id", "")

    if not action_id or not alert_id:
        raise ValueError("Missing action_id or value in action")

    # Extract user info
    user = payload.get("user", {})
    user_id = user.get("id", "")
    user_name = user.get("name") or user.get("username", "Unknown")

    # Extract response URL and channel
    response_url = payload.get("response_url", "")
    channel = payload.get("channel", {})
    channel_id = channel.get("id", "")

    # Extract original message blocks
    message = payload.get("message", {})
    original_blocks = message.get("blocks", [])

    return SlackInteraction(
        action_id=action_id,
        alert_id=alert_id,
        block_id=block_id,
        user_id=user_id,
        user_name=user_name,
        response_url=response_url,
        channel_id=channel_id,
        original_blocks=original_blocks,
    )


def build_confirmation_block(
    feedback_type: str,
    user_name: str,
) -> dict[str, Any]:
    """
    Build a confirmation block to replace the action buttons.

    Args:
        feedback_type: The feedback type (expected, unexpected, investigating).
        user_name: Name of user who provided feedback.

    Returns:
        Slack block with confirmation message.
    """
    emoji_map = {
        "expected": ":white_check_mark:",
        "unexpected": ":x:",
        "investigating": ":mag:",
    }
    emoji = emoji_map.get(feedback_type.lower(), ":memo:")

    label_map = {
        "expected": "Expected",
        "unexpected": "Unexpected",
        "investigating": "Investigating",
    }
    label = label_map.get(feedback_type.lower(), feedback_type)

    return {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"{emoji} Marked as *{label}* by {user_name}",
            }
        ],
    }


def replace_actions_with_confirmation(
    blocks: list[dict[str, Any]],
    alert_id: str,
    feedback_type: str,
    user_name: str,
) -> list[dict[str, Any]]:
    """
    Replace the feedback action buttons with a confirmation message.

    Args:
        blocks: Original message blocks.
        alert_id: Alert ID to match the actions block.
        feedback_type: The feedback type selected.
        user_name: Name of user who provided feedback.

    Returns:
        Updated blocks with actions replaced by confirmation.
    """
    confirmation = build_confirmation_block(feedback_type, user_name)
    target_block_id = f"anomaly_feedback_{alert_id}"

    updated_blocks = []
    for block in blocks:
        if block.get("block_id") == target_block_id:
            # Replace actions block with confirmation
            updated_blocks.append(confirmation)
        else:
            updated_blocks.append(block)

    return updated_blocks


def send_response_url_update(
    response_url: str,
    blocks: list[dict[str, Any]],
    replace_original: bool = True,
) -> None:
    """
    Update the original Slack message via response_url.

    Args:
        response_url: Slack response URL from the interaction payload.
        blocks: Updated blocks for the message.
        replace_original: Whether to replace the original message.

    Raises:
        Exception: If the update fails.
    """
    data = json.dumps({
        "replace_original": replace_original,
        "blocks": blocks,
    }).encode("utf-8")

    req = urllib.request.Request(
        response_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                print(f"Response URL update returned status {resp.status}")
    except Exception as e:
        print(f"Failed to update message via response_url: {e}")
        raise