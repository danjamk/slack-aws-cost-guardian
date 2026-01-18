"""Base classes for LLM provider abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMMessage:
    """Message for LLM conversation."""

    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class LLMResponse:
    """Response from LLM provider."""

    content: str
    model: str
    usage: dict[str, int]  # {"input_tokens": x, "output_tokens": y}
    finish_reason: str


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

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name."""
        pass