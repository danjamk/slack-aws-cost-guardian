"""Slack Block Kit message formatting."""

from datetime import datetime
from typing import Any

from slack_aws_cost_guardian.analysis.anomaly_detector import DetectedAnomaly
from slack_aws_cost_guardian.collectors.base import CostData
from slack_aws_cost_guardian.storage.models import BudgetStatus, CostSnapshot


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
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

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
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*AI Analysis*\n{ai_analysis}",
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

        return {"blocks": blocks}

    def format_daily_report(
        self,
        cost_data: CostData,
        budget_status: BudgetStatus | None = None,
        ai_insight: str | None = None,
    ) -> dict[str, Any]:
        """
        Format a daily cost summary report (no buttons - informational only).

        Args:
            cost_data: Collected cost data.
            budget_status: Optional budget information.
            ai_insight: Optional AI-generated insight.

        Returns:
            Slack Block Kit message payload.
        """
        date_str = datetime.utcnow().strftime("%B %d, %Y")
        trend_emoji = self.TREND_EMOJI.get(cost_data.trend, "")

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

        # Cost summary
        summary_parts = [f"*Yesterday's Spend*: ${cost_data.total_cost:.2f}"]

        if budget_status:
            summary_parts.append(
                f"*Month-to-Date*: ${budget_status.monthly_spent:.2f} "
                f"({budget_status.monthly_percent:.0f}% of ${budget_status.monthly_budget:.0f} budget)"
            )

        if cost_data.forecast:
            forecast_pct = (
                (cost_data.forecast.forecasted_total / budget_status.monthly_budget * 100)
                if budget_status and budget_status.monthly_budget > 0
                else 0
            )
            warning = " :warning:" if forecast_pct > 100 else ""
            summary_parts.append(
                f"*Projected Month-End*: ${cost_data.forecast.forecasted_total:.2f} "
                f"({forecast_pct:.0f}% of budget){warning}"
            )

        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(summary_parts)},
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
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":bulb: *AI Insight*\n{ai_insight}",
                    },
                }
            )

        # Footer
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Period: {cost_data.start_date} to {cost_data.end_date}",
                    }
                ],
            }
        )

        return {"blocks": blocks}

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
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":bulb: *Recommendation*\n{ai_recommendation}",
                    },
                }
            )

        return {"blocks": blocks}

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

        return {
            "blocks": [
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"{emoji} Marked as *{feedback_type}* by {user_name}",
                        }
                    ],
                }
            ]
        }

    def format_simple_message(self, text: str, emoji: str = ":robot_face:") -> dict[str, Any]:
        """Format a simple text message."""
        return {
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"{emoji} {text}"},
                }
            ]
        }