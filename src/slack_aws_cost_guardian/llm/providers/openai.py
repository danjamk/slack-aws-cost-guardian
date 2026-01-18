"""OpenAI API provider."""

from __future__ import annotations

import openai

from slack_aws_cost_guardian.config.schema import LLMConfig
from slack_aws_cost_guardian.llm.base import LLMMessage, LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

    def __init__(self, api_key: str, config: LLMConfig):
        """
        Initialize the OpenAI provider.

        Args:
            api_key: OpenAI API key.
            config: LLM configuration.
        """
        self.client = openai.OpenAI(api_key=api_key)
        self.config = config
        self.model_id = config.openai.model_id

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "openai"

    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """
        Send a chat completion request to OpenAI.

        Args:
            messages: List of messages for the conversation.
            **kwargs: Optional overrides for max_tokens, temperature.

        Returns:
            LLMResponse with OpenAI's response.
        """
        openai_messages = [{"role": m.role, "content": m.content} for m in messages]

        response = self.client.chat.completions.create(
            model=self.model_id,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature", self.config.temperature),
            messages=openai_messages,
        )

        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            usage={
                "input_tokens": response.usage.prompt_tokens if response.usage else 0,
                "output_tokens": response.usage.completion_tokens if response.usage else 0,
            },
            finish_reason=choice.finish_reason or "unknown",
        )