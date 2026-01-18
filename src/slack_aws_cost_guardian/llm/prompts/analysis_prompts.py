"""
Prompts for specific cost analysis tasks.

These prompts are combined with the system prompt and user context
to generate AI-powered insights.
"""


def build_anomaly_analysis_prompt(
    anomaly_data: dict,
    historical_context: str,
    user_context: str,
) -> str:
    """
    Build a prompt for analyzing a cost anomaly.

    Args:
        anomaly_data: Dict with service, current_cost, baseline_cost, etc.
        historical_context: Recent cost history summary.
        user_context: User's guardian-context.md content.
    """
    return f"""Analyze this AWS cost anomaly and provide insights.

## Anomaly Details
- Service: {anomaly_data.get('service', 'Unknown')}
- Current Cost: ${anomaly_data.get('current_cost', 0):.2f}
- Baseline Cost: ${anomaly_data.get('baseline_cost', 0):.2f}
- Change: ${anomaly_data.get('absolute_change', 0):+.2f} ({anomaly_data.get('percent_change', 0):+.1f}%)
- Severity: {anomaly_data.get('severity', 'unknown')}

## Historical Context
{historical_context}

## User's Environment
{user_context}

Provide:
1. Most likely explanation (1-2 sentences)
2. Potential causes to investigate
3. Recommended next steps
4. Risk assessment if left unaddressed

Keep your response concise and actionable.
"""


def build_daily_report_prompt(
    cost_summary: dict,
    user_context: str,
) -> str:
    """
    Build a prompt for daily cost report insights.

    Args:
        cost_summary: Dict with total_cost, top_services, trend, budget_percent.
        user_context: User's guardian-context.md content.
    """
    return f"""Provide a brief insight for this daily AWS cost summary.

## Today's Costs
- Total: ${cost_summary.get('total_cost', 0):.2f}
- Top Services: {cost_summary.get('top_services', [])}
- Trend: {cost_summary.get('trend', 'unknown')}
- Budget Status: {cost_summary.get('budget_percent', 0):.0f}% of monthly budget

## User's Environment
{user_context}

Provide a 1-2 sentence insight that's:
- Relevant to their specific situation
- Actionable if there's something to address
- Reassuring if things look normal

Do not repeat the numbers - focus on the "so what" interpretation.
"""


def build_weekly_report_prompt(
    weekly_summary: dict,
    user_context: str,
) -> str:
    """
    Build a prompt for weekly cost report insights.

    Args:
        weekly_summary: Dict with weekly cost data and trends.
        user_context: User's guardian-context.md content.
    """
    return f"""Provide insights for this weekly AWS cost summary.

## This Week
- Total Spend: ${weekly_summary.get('total_cost', 0):.2f}
- vs Last Week: {weekly_summary.get('week_over_week_change', 0):+.1f}%
- Top Services: {weekly_summary.get('top_services', [])}
- Anomalies: {weekly_summary.get('anomaly_count', 0)}

## Month-to-Date
- Spent: ${weekly_summary.get('mtd_cost', 0):.2f}
- Budget Used: {weekly_summary.get('budget_percent', 0):.0f}%
- Projected End-of-Month: ${weekly_summary.get('forecast', 0):.2f}

## User's Environment
{user_context}

Provide:
1. Key observation about the week (1 sentence)
2. Any concerns or positive trends
3. One actionable recommendation if applicable

Keep it brief - this is a summary, not a detailed report.
"""