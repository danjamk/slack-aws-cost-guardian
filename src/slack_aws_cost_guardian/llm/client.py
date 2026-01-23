"""LLM client with provider abstraction and secrets management."""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError

from slack_aws_cost_guardian.config.schema import LLMConfig
from slack_aws_cost_guardian.llm.base import LLMMessage, LLMProvider, LLMResponse, LLMTool
from slack_aws_cost_guardian.llm.providers import AnthropicProvider, OpenAIProvider
from slack_aws_cost_guardian.llm.tools.registry import ToolRegistry


class LLMClient:
    """
    LLM client with provider abstraction and secrets management.

    Retrieves API keys from Secrets Manager and creates the appropriate provider.
    Implements graceful degradation - returns None on failures instead of raising.
    """

    def __init__(
        self,
        config: LLMConfig,
        secret_name: str,
        region: str = "us-east-1",
    ):
        """
        Initialize the LLM client.

        Args:
            config: LLM configuration specifying provider and settings.
            secret_name: Secrets Manager secret name containing API keys.
            region: AWS region for Secrets Manager.
        """
        self.config = config
        self.secret_name = secret_name
        self.region = region
        self._provider: LLMProvider | None = None
        self._secrets_client = boto3.client("secretsmanager", region_name=region)

    def _get_api_key(self) -> str:
        """
        Retrieve API key from Secrets Manager.

        Returns:
            The API key for the configured provider.

        Raises:
            RuntimeError: If the secret cannot be retrieved or key is missing.
        """
        try:
            response = self._secrets_client.get_secret_value(SecretId=self.secret_name)
            secret_data = json.loads(response["SecretString"])

            # Key name depends on provider
            key_name = f"{self.config.provider}_api_key"
            api_key = secret_data.get(key_name)

            if not api_key:
                raise ValueError(f"API key '{key_name}' not found in secret")

            return api_key

        except ClientError as e:
            raise RuntimeError(f"Failed to retrieve LLM API key: {e}") from e

    def _get_provider(self) -> LLMProvider:
        """
        Get or create the LLM provider instance.

        Uses lazy initialization to avoid API key retrieval until needed.

        Returns:
            The configured LLM provider.
        """
        if self._provider is None:
            api_key = self._get_api_key()

            if self.config.provider == "anthropic":
                self._provider = AnthropicProvider(api_key, self.config)
            elif self.config.provider == "openai":
                self._provider = OpenAIProvider(api_key, self.config)
            else:
                raise ValueError(f"Unknown provider: {self.config.provider}")

        return self._provider

    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """
        Send a chat completion request.

        Args:
            messages: List of messages for the conversation.
            **kwargs: Provider-specific options.

        Returns:
            LLMResponse with the model's response.
        """
        provider = self._get_provider()
        return provider.chat(messages, **kwargs)

    def analyze_anomaly(
        self,
        anomaly_data: dict,
        historical_context: str,
        user_context: str,
        system_prompt: str,
    ) -> str | None:
        """
        Analyze an anomaly using the configured LLM.

        Implements graceful degradation - returns None on any failure,
        allowing the caller to proceed without AI analysis.

        Args:
            anomaly_data: Dictionary with anomaly details (service, costs, etc.)
            historical_context: Summary of recent cost history.
            user_context: User-specific context from guardian-context.md.
            system_prompt: System prompt defining AI behavior.

        Returns:
            Analysis text if successful, None on any failure.
        """
        from slack_aws_cost_guardian.llm.prompts import build_anomaly_analysis_prompt

        try:
            provider = self._get_provider()

            user_prompt = build_anomaly_analysis_prompt(
                anomaly_data=anomaly_data,
                historical_context=historical_context,
                user_context=user_context,
            )

            messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]

            response = provider.chat(messages)
            print(
                f"LLM analysis completed: {response.usage.get('input_tokens', 0)} in, "
                f"{response.usage.get('output_tokens', 0)} out"
            )
            return response.content

        except Exception as e:
            # Log error but return None for graceful degradation
            print(f"LLM analysis failed: {e}")
            return None

    def generate_daily_insight(
        self,
        daily_summary: dict,
        user_context: str,
        system_prompt: str,
    ) -> str | None:
        """
        Generate an insight for the daily cost summary.

        Args:
            daily_summary: Dict from build_daily_summary.
            user_context: User's guardian-context.md content.
            system_prompt: System prompt defining AI behavior.

        Returns:
            Insight text if successful, None on any failure.
        """
        from slack_aws_cost_guardian.llm.prompts import build_daily_report_prompt

        try:
            provider = self._get_provider()

            # Format top services for prompt
            top_services = [
                f"{s['service']}: ${s['cost']:.2f}"
                for s in daily_summary.get("top_services", [])
            ]

            cost_summary = {
                "total_cost": daily_summary.get("total_cost", 0),
                "top_services": top_services,
                "trend": daily_summary.get("trend", "unknown"),
                "budget_percent": daily_summary.get("budget_percent", 0),
            }

            user_prompt = build_daily_report_prompt(
                cost_summary=cost_summary,
                user_context=user_context,
            )

            messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]

            response = provider.chat(messages)
            print(
                f"Daily insight generated: {response.usage.get('input_tokens', 0)} in, "
                f"{response.usage.get('output_tokens', 0)} out"
            )
            return response.content

        except Exception as e:
            print(f"Daily insight generation failed: {e}")
            return None

    def generate_weekly_insight(
        self,
        weekly_summary: dict,
        user_context: str,
        system_prompt: str,
    ) -> str | None:
        """
        Generate an insight for the weekly cost summary.

        Args:
            weekly_summary: Dict from build_weekly_summary.
            user_context: User's guardian-context.md content.
            system_prompt: System prompt defining AI behavior.

        Returns:
            Insight text if successful, None on any failure.
        """
        from slack_aws_cost_guardian.llm.prompts import build_weekly_report_prompt

        try:
            provider = self._get_provider()

            # Format top services for prompt
            top_services = [
                f"{s['service']}: ${s['cost']:.2f}"
                for s in weekly_summary.get("top_services", [])
            ]

            prompt_summary = {
                "total_cost": weekly_summary.get("total_cost", 0),
                "week_over_week_change": weekly_summary.get("week_over_week_change", 0),
                "top_services": top_services,
                "anomaly_count": weekly_summary.get("anomaly_count", 0),
                "mtd_cost": weekly_summary.get("mtd_cost", 0),
                "budget_percent": weekly_summary.get("budget_percent", 0),
                "forecast": weekly_summary.get("forecast", 0),
            }

            user_prompt = build_weekly_report_prompt(
                weekly_summary=prompt_summary,
                user_context=user_context,
            )

            messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]

            response = provider.chat(messages)
            print(
                f"Weekly insight generated: {response.usage.get('input_tokens', 0)} in, "
                f"{response.usage.get('output_tokens', 0)} out"
            )
            return response.content

        except Exception as e:
            print(f"Weekly insight generation failed: {e}")
            return None

    def answer_cost_question(
        self,
        question: str,
        user_context: str | None,
        tool_registry: ToolRegistry,
        tools: list[LLMTool],
        system_prompt: str,
        max_iterations: int = 5,
    ) -> str | None:
        """
        Answer a user's cost question using tools.

        Implements a tool-use loop where the LLM can call tools to fetch data,
        then generate a final response.

        Args:
            question: The user's question about costs.
            user_context: Optional user context from guardian-context.md.
            tool_registry: Registry with tool implementations.
            tools: List of tool definitions for the LLM.
            system_prompt: System prompt for the cost query assistant.
            max_iterations: Maximum tool-use iterations (default 5).

        Returns:
            Answer text if successful, None on failure.
        """
        try:
            provider = self._get_provider()

            # Build initial messages
            full_system_prompt = system_prompt
            if user_context:
                full_system_prompt += f"\n\n## User's Infrastructure Context\n{user_context}"

            messages: list[LLMMessage] = [
                LLMMessage(role="system", content=full_system_prompt),
                LLMMessage(role="user", content=question),
            ]

            total_input_tokens = 0
            total_output_tokens = 0

            # Tool-use loop
            for iteration in range(max_iterations):
                print(f"Tool-use iteration {iteration + 1}/{max_iterations}")

                response = provider.chat_with_tools(messages, tools)
                total_input_tokens += response.usage.get("input_tokens", 0)
                total_output_tokens += response.usage.get("output_tokens", 0)

                # Check if we're done (no tool calls)
                if not response.tool_calls:
                    print(
                        f"Answer generated: {total_input_tokens} in, "
                        f"{total_output_tokens} out ({iteration + 1} iterations)"
                    )
                    return response.content

                # Execute tool calls
                print(f"Executing {len(response.tool_calls)} tool call(s)")

                # Add assistant message WITH tool calls
                # This is critical - the tool_use blocks must be in the assistant message
                # so that subsequent tool_result blocks have corresponding tool_use IDs
                messages.append(
                    LLMMessage(
                        role="assistant",
                        content=response.content or "",
                        tool_calls=response.tool_calls,
                    )
                )

                # Execute each tool and collect results
                tool_results = []
                for tool_call in response.tool_calls:
                    print(f"  Tool: {tool_call.name}({tool_call.arguments})")
                    result = tool_registry.execute(tool_call)
                    tool_results.append(result)

                    if result.is_error:
                        print(f"  Error: {result.content}")

                # Add all tool results as a single message
                # (Anthropic expects tool_results to follow the assistant's tool_use)
                for result in tool_results:
                    messages.append(
                        LLMMessage(
                            role="tool",
                            content=result.content,
                            tool_call_id=result.tool_call_id,
                        )
                    )

            # Max iterations reached without final answer
            print(f"Max iterations ({max_iterations}) reached without final answer")
            return "I'm having trouble finding the information. Could you try rephrasing your question?"

        except Exception as e:
            print(f"Cost question answering failed: {e}")
            return None