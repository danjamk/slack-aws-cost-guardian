"""OpenAI API provider."""

from __future__ import annotations

import json
from typing import Any

import openai

from slack_aws_cost_guardian.config.schema import LLMConfig
from slack_aws_cost_guardian.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResponse,
    LLMTool,
    LLMToolCall,
)


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

    def _convert_messages(self, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        """Convert LLMMessages to OpenAI format."""
        openai_messages: list[dict[str, Any]] = []

        for m in messages:
            if m.role == "tool":
                # Tool results in OpenAI format
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.content,
                })
            else:
                openai_messages.append({"role": m.role, "content": m.content})

        return openai_messages

    def _convert_tools(self, tools: list[LLMTool]) -> list[dict[str, Any]]:
        """Convert LLMTool to OpenAI function format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """
        Send a chat completion request to OpenAI.

        Args:
            messages: List of messages for the conversation.
            **kwargs: Optional overrides for max_tokens, temperature.

        Returns:
            LLMResponse with OpenAI's response.
        """
        openai_messages = self._convert_messages(messages)

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

    def chat_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[LLMTool],
        **kwargs,
    ) -> LLMResponse:
        """
        Send a chat completion request with tool definitions.

        Args:
            messages: List of messages for the conversation.
            tools: List of tool definitions available to OpenAI.
            **kwargs: Optional overrides for max_tokens, temperature.

        Returns:
            LLMResponse with OpenAI's response, potentially including tool_calls.
        """
        openai_messages = self._convert_messages(messages)
        openai_tools = self._convert_tools(tools)

        response = self.client.chat.completions.create(
            model=self.model_id,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature", self.config.temperature),
            messages=openai_messages,
            tools=openai_tools,
        )

        choice = response.choices[0]
        tool_calls: list[LLMToolCall] = []

        # Parse tool calls from response
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                tool_calls.append(
                    LLMToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=arguments,
                    )
                )

        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            usage={
                "input_tokens": response.usage.prompt_tokens if response.usage else 0,
                "output_tokens": response.usage.completion_tokens if response.usage else 0,
            },
            finish_reason=choice.finish_reason or "unknown",
            tool_calls=tool_calls,
        )