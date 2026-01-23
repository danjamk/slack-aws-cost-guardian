"""Slack Bot API client for sending messages."""

from __future__ import annotations

from typing import Any

import requests


class SlackBotClient:
    """
    Client for sending messages via Slack Bot API.

    Unlike webhooks (which are one-way), the Bot API allows:
    - Sending messages to any channel the bot is in
    - Replying in threads
    - Sending to DMs
    """

    BASE_URL = "https://slack.com/api"

    def __init__(self, bot_token: str):
        """
        Initialize the Slack Bot client.

        Args:
            bot_token: Slack Bot User OAuth Token (xoxb-...).
        """
        self.bot_token = bot_token
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json",
        })

    def send_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        """
        Send a text message to a channel or thread.

        Args:
            channel: Channel ID (C...), DM ID (D...), or user ID (U...).
            text: Message text (supports Slack mrkdwn formatting).
            thread_ts: Parent message timestamp to reply in thread. Optional.

        Returns:
            Slack API response dict with 'ok', 'ts', etc.
        """
        payload: dict[str, Any] = {
            "channel": channel,
            "text": text,
        }

        if thread_ts:
            payload["thread_ts"] = thread_ts

        return self._post("chat.postMessage", payload)

    def send_blocks(
        self,
        channel: str,
        blocks: list[dict[str, Any]],
        text: str = "",
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        """
        Send a Block Kit message to a channel or thread.

        Args:
            channel: Channel ID (C...), DM ID (D...), or user ID (U...).
            blocks: List of Block Kit blocks.
            text: Fallback text for notifications. Optional but recommended.
            thread_ts: Parent message timestamp to reply in thread. Optional.

        Returns:
            Slack API response dict with 'ok', 'ts', etc.
        """
        payload: dict[str, Any] = {
            "channel": channel,
            "blocks": blocks,
            "text": text or "New message from Cost Guardian",
        }

        if thread_ts:
            payload["thread_ts"] = thread_ts

        return self._post("chat.postMessage", payload)

    def add_reaction(
        self,
        channel: str,
        timestamp: str,
        name: str,
    ) -> dict[str, Any]:
        """
        Add a reaction emoji to a message.

        Args:
            channel: Channel ID where the message is.
            timestamp: Message timestamp (ts).
            name: Emoji name without colons (e.g., 'thumbsup').

        Returns:
            Slack API response dict.
        """
        payload = {
            "channel": channel,
            "timestamp": timestamp,
            "name": name,
        }
        return self._post("reactions.add", payload)

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Make a POST request to the Slack API.

        Args:
            method: Slack API method (e.g., 'chat.postMessage').
            payload: Request payload.

        Returns:
            Response JSON.

        Raises:
            requests.RequestException: On network errors.
        """
        url = f"{self.BASE_URL}/{method}"

        try:
            response = self._session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                error = data.get("error", "unknown_error")
                print(f"Slack API error ({method}): {error}")

            return data

        except requests.RequestException as e:
            print(f"Slack API request failed ({method}): {e}")
            return {"ok": False, "error": str(e)}