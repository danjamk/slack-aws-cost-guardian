"""Anthropic Claude API provider."""

from __future__ import annotations

import anthropic

from slack_aws_cost_guardian.config.schema import LLMConfig
from slack_aws_cost_guardian.llm.base import LLMMessage, LLMProvider, LLMResponse


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider."""

    def __init__(self, api_key: str, config: LLMConfig):
        """
        Initialize the Anthropic provider.

        Args:
            api_key: Anthropic API key.
            config: LLM configuration.
        """
        self.client = anthropic.Anthropic(api_key=api_key)
        self.config = config
        self.model_id = config.anthropic.model_id

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "anthropic"

    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """
        Send a chat completion request to Claude.

        Args:
            messages: List of messages for the conversation.
            **kwargs: Optional overrides for max_tokens, temperature.

        Returns:
            LLMResponse with Claude's response.
        """
        # Extract system message (Anthropic handles it separately)
        system_msg = next((m.content for m in messages if m.role == "system"), "")
        user_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]

        response = self.client.messages.create(
            model=self.model_id,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature", self.config.temperature),
            system=system_msg,
            messages=user_messages,
        )

        return LLMResponse(
            content=response.content[0].text,
            model=response.model,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            finish_reason=response.stop_reason or "unknown",
        )