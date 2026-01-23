"""Anthropic Claude API provider."""

from __future__ import annotations

from typing import Any

import anthropic

from slack_aws_cost_guardian.config.schema import LLMConfig
from slack_aws_cost_guardian.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResponse,
    LLMTool,
    LLMToolCall,
)


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

    def _convert_messages(
        self, messages: list[LLMMessage]
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Convert LLMMessages to Anthropic format.

        Returns:
            Tuple of (system_message, user_messages).
        """
        system_msg = next((m.content for m in messages if m.role == "system"), "")
        api_messages: list[dict[str, Any]] = []

        # Collect consecutive tool results to merge into single user message
        pending_tool_results: list[dict[str, Any]] = []

        for m in messages:
            if m.role == "system":
                continue
            elif m.role == "tool":
                # Collect tool results (will be merged into single user message)
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content,
                })
            else:
                # Before adding non-tool message, flush pending tool results
                if pending_tool_results:
                    api_messages.append({
                        "role": "user",
                        "content": pending_tool_results,
                    })
                    pending_tool_results = []

                if m.role == "assistant" and m.tool_calls:
                    # Assistant message with tool use - include both text and tool_use blocks
                    content_blocks: list[dict[str, Any]] = []

                    # Add text content if present
                    if m.content:
                        content_blocks.append({"type": "text", "text": m.content})

                    # Add tool_use blocks
                    for tc in m.tool_calls:
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        })

                    api_messages.append({
                        "role": "assistant",
                        "content": content_blocks,
                    })
                else:
                    api_messages.append({"role": m.role, "content": m.content})

        # Flush any remaining tool results
        if pending_tool_results:
            api_messages.append({
                "role": "user",
                "content": pending_tool_results,
            })

        return system_msg, api_messages

    def _convert_tools(self, tools: list[LLMTool]) -> list[dict[str, Any]]:
        """Convert LLMTool to Anthropic tool format."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """
        Send a chat completion request to Claude.

        Args:
            messages: List of messages for the conversation.
            **kwargs: Optional overrides for max_tokens, temperature.

        Returns:
            LLMResponse with Claude's response.
        """
        system_msg, user_messages = self._convert_messages(messages)

        response = self.client.messages.create(
            model=self.model_id,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature", self.config.temperature),
            system=system_msg,
            messages=user_messages,
        )

        # Extract text content
        text_content = ""
        for block in response.content:
            if block.type == "text":
                text_content = block.text
                break

        return LLMResponse(
            content=text_content,
            model=response.model,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            finish_reason=response.stop_reason or "unknown",
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
            tools: List of tool definitions available to Claude.
            **kwargs: Optional overrides for max_tokens, temperature.

        Returns:
            LLMResponse with Claude's response, potentially including tool_calls.
        """
        system_msg, user_messages = self._convert_messages(messages)
        api_tools = self._convert_tools(tools)

        response = self.client.messages.create(
            model=self.model_id,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature", self.config.temperature),
            system=system_msg,
            messages=user_messages,
            tools=api_tools,
        )

        # Parse response content
        text_content = ""
        tool_calls: list[LLMToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_content = block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    LLMToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )

        return LLMResponse(
            content=text_content,
            model=response.model,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            finish_reason=response.stop_reason or "unknown",
            tool_calls=tool_calls,
        )