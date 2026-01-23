"""Base classes for LLM provider abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMMessage:
    """Message for LLM conversation."""

    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_call_id: str | None = None  # For tool result messages
    tool_calls: list["LLMToolCall"] | None = None  # For assistant messages with tool use


@dataclass
class LLMToolCall:
    """Represents a tool call from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMToolResult:
    """Result from executing a tool."""

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class LLMResponse:
    """Response from LLM provider."""

    content: str
    model: str
    usage: dict[str, int]  # {"input_tokens": x, "output_tokens": y}
    finish_reason: str
    tool_calls: list[LLMToolCall] = field(default_factory=list)


@dataclass
class LLMTool:
    """Tool definition for LLM."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """
        Send a chat completion request.

        Args:
            messages: List of messages for the conversation.
            **kwargs: Provider-specific options (max_tokens, temperature, etc.)

        Returns:
            LLMResponse with the model's response.
        """
        pass

    @abstractmethod
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
            tools: List of tool definitions available to the LLM.
            **kwargs: Provider-specific options (max_tokens, temperature, etc.)

        Returns:
            LLMResponse with the model's response, potentially including tool_calls.
        """
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name."""
        pass