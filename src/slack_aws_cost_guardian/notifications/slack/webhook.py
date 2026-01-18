"""Slack webhook notification sender."""

from __future__ import annotations

import json
from typing import Any
from urllib import request, error

import boto3
from botocore.exceptions import ClientError


class SlackWebhookError(Exception):
    """Error sending Slack webhook."""

    pass


class SlackWebhook:
    """
    Send messages to Slack via webhooks.

    Webhook URLs are stored in AWS Secrets Manager for security.
    """

    def __init__(
        self,
        secret_name: str,
        secret_key: str,
        region: str = "us-east-1",
        secrets_client: boto3.client | None = None,
    ):
        """
        Initialize the Slack webhook sender.

        Args:
            secret_name: Name of the secret in Secrets Manager.
            secret_key: Key within the secret containing the webhook URL.
            region: AWS region for Secrets Manager.
            secrets_client: Optional boto3 Secrets Manager client.
        """
        self.secret_name = secret_name
        self.secret_key = secret_key
        self.region = region
        self._secrets_client = secrets_client
        self._webhook_url: str | None = None

    @property
    def secrets_client(self) -> boto3.client:
        """Get or create Secrets Manager client."""
        if self._secrets_client is None:
            self._secrets_client = boto3.client(
                "secretsmanager", region_name=self.region
            )
        return self._secrets_client

    @property
    def webhook_url(self) -> str:
        """Get the webhook URL from Secrets Manager."""
        if self._webhook_url is None:
            self._webhook_url = self._get_webhook_url()
        return self._webhook_url

    def _get_webhook_url(self) -> str:
        """Retrieve webhook URL from Secrets Manager."""
        try:
            response = self.secrets_client.get_secret_value(SecretId=self.secret_name)

            if "SecretString" in response:
                secret_data = json.loads(response["SecretString"])
                if self.secret_key not in secret_data:
                    raise SlackWebhookError(
                        f"Secret key '{self.secret_key}' not found in secret '{self.secret_name}'"
                    )
                return secret_data[self.secret_key]
            else:
                raise SlackWebhookError(
                    f"Secret '{self.secret_name}' does not contain a string value"
                )

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise SlackWebhookError(f"Secret '{self.secret_name}' not found")
            raise SlackWebhookError(f"Error retrieving secret: {e}")

    def send(self, message: dict[str, Any]) -> bool:
        """
        Send a message to Slack.

        Args:
            message: Slack Block Kit message payload.

        Returns:
            True if message was sent successfully.

        Raises:
            SlackWebhookError: If the message fails to send.
        """
        try:
            data = json.dumps(message).encode("utf-8")

            req = request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with request.urlopen(req, timeout=10) as response:
                response_body = response.read().decode("utf-8")

                if response.status != 200 or response_body != "ok":
                    raise SlackWebhookError(
                        f"Slack API error: {response.status} - {response_body}"
                    )

            return True

        except error.HTTPError as e:
            raise SlackWebhookError(f"HTTP error sending to Slack: {e.code} - {e.reason}")
        except error.URLError as e:
            raise SlackWebhookError(f"URL error sending to Slack: {e.reason}")
        except Exception as e:
            raise SlackWebhookError(f"Error sending to Slack: {e}")

    def send_text(self, text: str) -> bool:
        """
        Send a simple text message to Slack.

        Args:
            text: Plain text message.

        Returns:
            True if message was sent successfully.
        """
        return self.send({"text": text})


class SlackWebhookManager:
    """
    Manage multiple Slack webhooks for different channels.

    Channels are configured with their webhook URLs stored in Secrets Manager.
    """

    def __init__(
        self,
        secret_name: str,
        region: str = "us-east-1",
        secrets_client: boto3.client | None = None,
    ):
        """
        Initialize the webhook manager.

        Args:
            secret_name: Name of the secret containing all webhook URLs.
            region: AWS region.
            secrets_client: Optional boto3 Secrets Manager client.
        """
        self.secret_name = secret_name
        self.region = region
        self._secrets_client = secrets_client
        self._webhooks: dict[str, SlackWebhook] = {}

    def get_webhook(self, channel_key: str) -> SlackWebhook:
        """
        Get a webhook sender for a specific channel.

        Args:
            channel_key: The key for the channel's webhook URL in the secret.

        Returns:
            SlackWebhook instance for the channel.
        """
        if channel_key not in self._webhooks:
            self._webhooks[channel_key] = SlackWebhook(
                secret_name=self.secret_name,
                secret_key=channel_key,
                region=self.region,
                secrets_client=self._secrets_client,
            )
        return self._webhooks[channel_key]

    def send_to_channel(self, channel_key: str, message: dict[str, Any]) -> bool:
        """
        Send a message to a specific channel.

        Args:
            channel_key: The channel's webhook key.
            message: Slack Block Kit message payload.

        Returns:
            True if successful.
        """
        webhook = self.get_webhook(channel_key)
        return webhook.send(message)