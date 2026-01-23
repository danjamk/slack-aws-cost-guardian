"""Tool schemas and system prompts for cost queries."""

from __future__ import annotations

from slack_aws_cost_guardian.llm.base import LLMTool

# System prompt for cost query assistant
COST_QUERY_SYSTEM_PROMPT = """You are Cost Guardian, an AI assistant that helps users understand their AWS spending.

You have access to tools that can query AWS cost data. Use these tools to answer questions about:
- Daily and historical costs
- Service-level cost breakdowns
- Cost trends over time
- Account-level cost allocation

When answering:
1. Use the appropriate tool(s) to fetch the data needed
2. Present costs clearly with currency (USD)
3. Provide context when helpful (comparisons to averages, trends)
4. Keep responses concise but informative
5. If you can't answer a question with the available tools, explain what information you'd need

Format costs as: $X.XX (e.g., $142.50)
Format percentages as: X% (e.g., 15%)

Be helpful and proactive - if a user asks about "yesterday", use the tool appropriately.
If there's an error fetching data, explain it clearly and suggest alternatives.
"""

# Tool definitions compatible with both Claude and OpenAI
COST_TOOLS: list[LLMTool] = [
    LLMTool(
        name="get_daily_costs",
        description="Get cost summary for a specific date or date range. Returns total cost, breakdown by service, and daily trends.",
        parameters={
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format, or 'yesterday', 'today', or relative like '7_days_ago'",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format. Optional, defaults to start_date for single day.",
                },
                "account_id": {
                    "type": "string",
                    "description": "Filter to specific AWS account ID. Optional.",
                },
            },
            "required": ["start_date"],
        },
    ),
    LLMTool(
        name="get_service_trend",
        description="Get cost trend for a specific AWS service over time. Shows daily costs and calculates trend direction.",
        parameters={
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "AWS service name (e.g., 'Amazon Elastic Compute Cloud - Compute', 'Amazon Relational Database Service', 'AWS Lambda'). Use the full service name.",
                },
                "period": {
                    "type": "string",
                    "enum": ["7d", "14d", "30d"],
                    "description": "Time period: 7d (week), 14d (two weeks), 30d (month)",
                },
                "account_id": {
                    "type": "string",
                    "description": "Filter to specific AWS account ID. Optional.",
                },
            },
            "required": ["service", "period"],
        },
    ),
    LLMTool(
        name="get_account_breakdown",
        description="Get cost breakdown by AWS account for a date range. Useful for multi-account organizations.",
        parameters={
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format, or 'yesterday', '7_days_ago', etc.",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format. Optional.",
                },
            },
            "required": ["start_date"],
        },
    ),
    LLMTool(
        name="get_top_services",
        description="Get the top N services by cost for a date range. Returns services sorted by cost descending.",
        parameters={
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format, or 'yesterday', '7_days_ago', etc.",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format. Optional.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of top services to return. Default 10, max 20.",
                },
            },
            "required": ["start_date"],
        },
    ),
]