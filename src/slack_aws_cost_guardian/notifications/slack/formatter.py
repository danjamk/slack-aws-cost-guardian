"""Slack Block Kit message formatting."""

import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from slack_aws_cost_guardian.analysis.anomaly_detector import DetectedAnomaly
from slack_aws_cost_guardian.collectors.base import CostData
from slack_aws_cost_guardian.storage.models import BudgetStatus, CostSnapshot

# US Central timezone (handles DST automatically)
CENTRAL_TZ = ZoneInfo("America/Chicago")


def _get_central_timestamp() -> str:
    """Get current timestamp formatted for US Central time."""
    now_utc = datetime.now(UTC)
    now_central = now_utc.astimezone(CENTRAL_TZ)
    # Use %Z to show CST or CDT depending on DST
    return now_central.strftime("%b %d, %Y at %I:%M %p %Z")


def _markdown_to_mrkdwn(text: str) -> str:
    """
    Convert standard markdown to Slack mrkdwn format.

    Slack mrkdwn differences:
    - Bold: *text* (not **text**)
    - Italic: _text_ (same)
    - Links: <url|text> (not [text](url))
    - No heading support (# converted to bold)
    - Code: `text` (same)
    """
    if not text:
        return text

    # Convert **bold** to *bold*
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)

    # Convert [text](url) to <url|text>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', text)

    # Convert markdown headings to bold text
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)

    # Convert bullet points (already work but ensure consistency)
    text = re.sub(r'^[\-\*]\s+', '• ', text, flags=re.MULTILINE)

    return text


class SlackFormatter:
    """Format messages using Slack Block Kit."""

    SEVERITY_EMOJI = {
        "critical": ":rotating_light:",
        "warning": ":warning:",
        "info": ":information_source:",
    }

    TREND_EMOJI = {
        "increasing": ":chart_with_upwards_trend:",
        "decreasing": ":chart_with_downwards_trend:",
        "stable": ":left_right_arrow:",
        "unknown": ":grey_question:",
    }

    def format_anomaly_alert(
        self,
        anomaly: DetectedAnomaly,
        alert_id: str,
        ai_analysis: str | None = None,
    ) -> dict[str, Any]:
        """
        Format an anomaly alert with interactive feedback buttons.

        Args:
            anomaly: The detected anomaly.
            alert_id: Unique ID for this alert (for button callbacks).
            ai_analysis: Optional AI-generated analysis text.

        Returns:
            Slack Block Kit message payload.
        """
        emoji = self.SEVERITY_EMOJI.get(anomaly.severity, ":grey_question:")
        timestamp = _get_central_timestamp()

        blocks = [
            # Header
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} Cost Anomaly Detected",
                    "emoji": True,
                },
            },
            # Anomaly details
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Service*\n{anomaly.service}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Change*\n${anomaly.absolute_change:+.2f} ({anomaly.percent_change:+.0f}%)",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Current Cost*\n${anomaly.current_cost:.2f}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Baseline*\n${anomaly.baseline_cost:.2f}",
                    },
                ],
            },
            {"type": "divider"},
        ]

        # AI Analysis section (if provided)
        if ai_analysis:
            # Convert markdown to Slack mrkdwn format
            formatted_analysis = _markdown_to_mrkdwn(ai_analysis)
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*AI Analysis*\n{formatted_analysis}",
                    },
                }
            )
            blocks.append({"type": "divider"})

        # Feedback buttons
        blocks.append(
            {
                "type": "actions",
                "block_id": f"anomaly_feedback_{alert_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":white_check_mark: Expected", "emoji": True},
                        "style": "primary",
                        "action_id": "feedback_expected",
                        "value": alert_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":x: Unexpected", "emoji": True},
                        "style": "danger",
                        "action_id": "feedback_unexpected",
                        "value": alert_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":mag: Investigating", "emoji": True},
                        "action_id": "feedback_investigating",
                        "value": alert_id,
                    },
                ],
            }
        )

        # Footer with metadata
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Alert ID: `{alert_id[:8]}` | {timestamp} | Severity: {anomaly.severity}",
                    }
                ],
            }
        )

        return {
            "username": "AWS Cost Guardian",
            "icon_emoji": ":shield:",
            "blocks": blocks,
        }

    def format_daily_report(
        self,
        cost_data: CostData,
        budget_status: BudgetStatus | None = None,
        ai_insight: str | None = None,
        report_date: str | None = None,
        cost_data_date: str | None = None,
        provider_costs: dict[str, float] | None = None,
        used_fallback: bool = False,
    ) -> dict[str, Any]:
        """
        Format a daily cost summary report (no buttons - informational only).

        Args:
            cost_data: Collected cost data.
            budget_status: Optional budget information.
            ai_insight: Optional AI-generated insight.
            report_date: The date the snapshot was taken (YYYY-MM-DD). Defaults to today.
            cost_data_date: The actual date the cost data represents (may differ due to lag).
            provider_costs: Dict with "aws" and "claude" keys containing costs per provider.
            used_fallback: If True, indicates today's data is being used instead of yesterday's.

        Returns:
            Slack Block Kit message payload.
        """
        # Use cost_data_date if provided (the actual date costs represent)
        # Fall back to report_date or today
        display_date = cost_data_date or report_date
        if display_date:
            try:
                dt = datetime.fromisoformat(display_date)
                date_str = dt.strftime("%B %d, %Y")
            except ValueError:
                date_str = display_date
        else:
            date_str = datetime.now(UTC).strftime("%B %d, %Y")

        trend_emoji = self.TREND_EMOJI.get(cost_data.trend, "")
        spend_label = "Today" if used_fallback else "Yesterday"

        # Calculate provider costs if not provided
        if provider_costs is None:
            aws_cost = 0.0
            claude_cost = 0.0
            for service, cost in cost_data.cost_by_service.items():
                if service.startswith("Claude::"):
                    claude_cost += cost
                else:
                    aws_cost += cost
            provider_costs = {"aws": aws_cost, "claude": claude_cost}

        aws_cost = provider_costs.get("aws", 0.0)
        claude_cost = provider_costs.get("claude", 0.0)
        has_claude = claude_cost > 0

        blocks = [
            # Header
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f":bar_chart: Daily Cost Report - {date_str}",
                    "emoji": True,
                },
            },
        ]

        # Add fallback note if using today's data
        if used_fallback:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": ":information_source: _Using today's data (yesterday's data not yet available)_",
                        }
                    ],
                }
            )

        # AWS Costs section
        aws_lines = [f"*:aws: AWS Costs*"]
        aws_lines.append(f"• {spend_label}: ${aws_cost:.2f}")

        if budget_status:
            # Budget status is AWS-only (from AWS Budgets API)
            aws_lines.append(
                f"• Month-to-Date: ${budget_status.monthly_spent:.2f} "
                f"({budget_status.monthly_percent:.0f}% of ${budget_status.monthly_budget:.0f} budget)"
            )

        if cost_data.forecast:
            # Forecast is AWS-only (from AWS Cost Explorer API)
            forecast_pct = (
                (cost_data.forecast.forecasted_total / budget_status.monthly_budget * 100)
                if budget_status and budget_status.monthly_budget > 0
                else 0
            )
            warning = " :warning:" if forecast_pct > 100 else ""
            aws_lines.append(
                f"• Forecast: ${cost_data.forecast.forecasted_total:.2f} "
                f"({forecast_pct:.0f}% of budget){warning}"
            )

        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(aws_lines)},
            }
        )

        # Claude API Costs section (only if there are Claude costs)
        if has_claude:
            claude_lines = [f"*:robot_face: Claude API Costs*"]
            claude_lines.append(f"• {spend_label}: ${claude_cost:.2f}")
            # Note: No forecast available for Claude - would need Anthropic API for that

            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "\n".join(claude_lines)},
                }
            )

            # Combined Total section
            combined_lines = [f"*:moneybag: Combined Total*"]
            combined_lines.append(f"• {spend_label}: ${cost_data.total_cost:.2f}")

            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "\n".join(combined_lines)},
                }
            )

        blocks.append({"type": "divider"})

        # Top services
        top_services = sorted(
            cost_data.cost_by_service.items(), key=lambda x: x[1], reverse=True
        )[:5]

        if top_services:
            total = sum(cost_data.cost_by_service.values())
            service_lines = ["*Top 5 Services*:"]
            for i, (service, cost) in enumerate(top_services, 1):
                pct = (cost / total * 100) if total > 0 else 0
                service_lines.append(f"{i}. {service}: ${cost:.2f} ({pct:.0f}%)")

            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "\n".join(service_lines)},
                }
            )

        # Trend
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Trend*: {trend_emoji} {cost_data.trend.title()} (avg ${cost_data.average_daily_cost:.2f}/day)",
                },
            }
        )

        # AI Insight (if provided)
        if ai_insight:
            formatted_insight = _markdown_to_mrkdwn(ai_insight)
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":bulb: *AI Insight*\n{formatted_insight}",
                    },
                }
            )

        # Footer with timestamp
        timestamp = _get_central_timestamp()
        footer_text = f"Period: {cost_data.start_date} to {cost_data.end_date} | Generated {timestamp}"
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": footer_text,
                    }
                ],
            }
        )

        # Show recent incomplete data (days after cost_data_date) if available
        # These are more recent days that may not be fully populated yet
        if cost_data.daily_costs:
            sorted_costs = sorted(cost_data.daily_costs, key=lambda x: x.date)

            if sorted_costs:
                recent_lines = []
                for dc in sorted_costs:
                    recent_lines.append(f"{dc.date}: ${dc.cost:.2f}")

                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f":hourglass_flowing_sand: _Recent data (still populating): {' | '.join(recent_lines)}_",
                            }
                        ],
                    }
                )

        # Add latency warning
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "_Note: AWS Cost Explorer data takes 24-48 hours to fully populate._",
                    }
                ],
            }
        )

        return {
            "username": "AWS Cost Guardian",
            "icon_emoji": ":bar_chart:",
            "blocks": blocks,
        }

    def format_weekly_report(
        self,
        weekly_summary: dict,
        ai_insight: str | None = None,
    ) -> dict[str, Any]:
        """
        Format a weekly cost summary report (no buttons - informational only).

        Args:
            weekly_summary: Dict from build_weekly_summary with cost data.
            ai_insight: Optional AI-generated insight.

        Returns:
            Slack Block Kit message payload.
        """
        start_date = weekly_summary.get("start_date", "")
        end_date = weekly_summary.get("end_date", "")
        total_cost = weekly_summary.get("total_cost", 0)
        wow_change = weekly_summary.get("week_over_week_change", 0)
        top_services = weekly_summary.get("top_services", [])
        anomaly_count = weekly_summary.get("anomaly_count", 0)
        mtd_cost = weekly_summary.get("mtd_cost", 0)
        budget_percent = weekly_summary.get("budget_percent", 0)
        forecast = weekly_summary.get("forecast", 0)
        daily_avg = weekly_summary.get("daily_average", 0)

        # Determine trend emoji
        if wow_change > 10:
            trend_emoji = ":chart_with_upwards_trend:"
            change_text = f"+{wow_change:.1f}% vs last week"
        elif wow_change < -10:
            trend_emoji = ":chart_with_downwards_trend:"
            change_text = f"{wow_change:.1f}% vs last week"
        else:
            trend_emoji = ":left_right_arrow:"
            change_text = f"{wow_change:+.1f}% vs last week (stable)"

        blocks = [
            # Header
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":calendar: Weekly Cost Report",
                    "emoji": True,
                },
            },
            # Period
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Period*: {start_date} to {end_date}",
                    }
                ],
            },
        ]

        # Week summary
        blocks.append(
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Week Total*\n${total_cost:.2f}"},
                    {"type": "mrkdwn", "text": f"*Daily Average*\n${daily_avg:.2f}"},
                    {"type": "mrkdwn", "text": f"*Change*\n{trend_emoji} {change_text}"},
                    {"type": "mrkdwn", "text": f"*Anomalies*\n{anomaly_count} detected"},
                ],
            }
        )

        blocks.append({"type": "divider"})

        # Top services
        if top_services:
            service_lines = ["*Top 5 Services This Week*:"]
            total = sum(s["cost"] for s in top_services)
            for i, svc in enumerate(top_services, 1):
                pct = (svc["cost"] / total * 100) if total > 0 else 0
                service_lines.append(f"{i}. {svc['service']}: ${svc['cost']:.2f} ({pct:.0f}%)")

            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "\n".join(service_lines)},
                }
            )

        blocks.append({"type": "divider"})

        # Month-to-date and forecast
        forecast_warning = " :warning:" if budget_percent > 90 else ""
        blocks.append(
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Month-to-Date*\n${mtd_cost:.2f} ({budget_percent:.0f}% of budget)"},
                    {"type": "mrkdwn", "text": f"*Projected Month-End*\n${forecast:.2f}{forecast_warning}"},
                ],
            }
        )

        # AI Insight (if provided)
        if ai_insight:
            formatted_insight = _markdown_to_mrkdwn(ai_insight)
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":bulb: *AI Insight*\n{formatted_insight}",
                    },
                }
            )

        # Footer with timestamp
        timestamp = _get_central_timestamp()
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Generated {timestamp}",
                    }
                ],
            }
        )

        return {
            "username": "AWS Cost Guardian",
            "icon_emoji": ":calendar:",
            "blocks": blocks,
        }

    def format_budget_alert(
        self,
        budget_status: BudgetStatus,
        threshold_type: str,  # "warning" or "critical"
        ai_recommendation: str | None = None,
    ) -> dict[str, Any]:
        """
        Format a budget threshold alert (informational - no buttons).

        Args:
            budget_status: Current budget status.
            threshold_type: Type of threshold crossed.
            ai_recommendation: Optional AI-generated recommendation.

        Returns:
            Slack Block Kit message payload.
        """
        emoji = ":moneybag:" if threshold_type == "warning" else ":rotating_light:"
        threshold_pct = 80 if threshold_type == "warning" else 100

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} Budget Alert: {threshold_pct}% Threshold Reached",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Your monthly AWS budget has reached {budget_status.monthly_percent:.0f}% utilization.",
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Budget*\n${budget_status.monthly_budget:.2f}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Current Spend*\n${budget_status.monthly_spent:.2f} ({budget_status.monthly_percent:.0f}%)",
                    },
                ],
            },
        ]

        if ai_recommendation:
            formatted_rec = _markdown_to_mrkdwn(ai_recommendation)
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":bulb: *Recommendation*\n{formatted_rec}",
                    },
                }
            )

        # Footer with timestamp
        timestamp = _get_central_timestamp()
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Generated {timestamp}",
                    }
                ],
            }
        )

        return {
            "username": "AWS Cost Guardian",
            "icon_emoji": ":moneybag:",
            "blocks": blocks,
        }

    def format_feedback_confirmation(
        self,
        original_alert_id: str,
        feedback_type: str,
        user_name: str,
    ) -> dict[str, Any]:
        """
        Format a confirmation message after user provides feedback.

        This replaces the buttons in the original message.
        """
        emoji_map = {
            "expected": ":white_check_mark:",
            "unexpected": ":x:",
            "investigating": ":mag:",
        }
        emoji = emoji_map.get(feedback_type, ":grey_question:")

        timestamp = _get_central_timestamp()
        return {
            "username": "AWS Cost Guardian",
            "icon_emoji": ":shield:",
            "blocks": [
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"{emoji} Marked as *{feedback_type}* by {user_name} at {timestamp}",
                        }
                    ],
                }
            ]
        }

    def format_simple_message(self, text: str, emoji: str = ":robot_face:") -> dict[str, Any]:
        """Format a simple text message."""
        return {
            "username": "AWS Cost Guardian",
            "icon_emoji": ":shield:",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"{emoji} {text}"},
                }
            ]
        }