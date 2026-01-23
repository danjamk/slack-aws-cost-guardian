"""Tool registry for dispatching tool calls."""

from __future__ import annotations

import json
from typing import Any, Callable

from slack_aws_cost_guardian.llm.base import LLMToolCall, LLMToolResult


class ToolRegistry:
    """Registry for LLM tools with dispatch capabilities."""

    def __init__(self):
        """Initialize an empty tool registry."""
        self._tools: dict[str, Callable[..., dict[str, Any]]] = {}

    def register(self, name: str, func: Callable[..., dict[str, Any]]) -> None:
        """
        Register a tool function.

        Args:
            name: Tool name (must match schema name).
            func: Function to call. Should return a dict that will be JSON serialized.
        """
        self._tools[name] = func

    def execute(self, tool_call: LLMToolCall) -> LLMToolResult:
        """
        Execute a tool call and return the result.

        Args:
            tool_call: The tool call from the LLM.

        Returns:
            LLMToolResult with the tool output or error message.
        """
        if tool_call.name not in self._tools:
            return LLMToolResult(
                tool_call_id=tool_call.id,
                content=json.dumps({"error": f"Unknown tool: {tool_call.name}"}),
                is_error=True,
            )

        try:
            func = self._tools[tool_call.name]
            result = func(**tool_call.arguments)
            return LLMToolResult(
                tool_call_id=tool_call.id,
                content=json.dumps(result),
                is_error=False,
            )
        except TypeError as e:
            # Invalid arguments
            return LLMToolResult(
                tool_call_id=tool_call.id,
                content=json.dumps({"error": f"Invalid arguments: {e}"}),
                is_error=True,
            )
        except Exception as e:
            # Tool execution error
            return LLMToolResult(
                tool_call_id=tool_call.id,
                content=json.dumps({"error": f"Tool error: {e}"}),
                is_error=True,
            )

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())