"""Build daily and weekly summary reports from cost snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from slack_aws_cost_guardian.storage.dynamodb import DynamoDBStorage
from slack_aws_cost_guardian.storage.models import CostSnapshot


def build_daily_summary(
    storage: DynamoDBStorage,
    target_date: str | None = None,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    """
    Build a summary of the previous day's costs.

    Args:
        storage: DynamoDB storage client.
        target_date: Date to summarize (YYYY-MM-DD). Defaults to yesterday.
        allow_fallback: If True and no data for target_date, try today's data.

    Returns:
        Dict with: total_cost, top_services, trend, budget_percent, etc.
    """
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)

    if target_date is None:
        target_date = yesterday.isoformat()

    # Get snapshots for the target date
    snapshots = storage.get_snapshots_for_date(target_date)
    used_fallback = False

    # Fallback to today if no data for yesterday
    if not snapshots and allow_fallback and target_date == yesterday.isoformat():
        today_str = today.isoformat()
        snapshots = storage.get_snapshots_for_date(today_str)
        if snapshots:
            target_date = today_str
            used_fallback = True

    if not snapshots:
        return {
            "date": target_date,
            "total_cost": 0.0,
            "top_services": [],
            "trend": "unknown",
            "budget_percent": 0.0,
            "budget_monthly": 0.0,
            "budget_spent": 0.0,
            "forecast": 0.0,
            "has_data": False,
            "used_fallback": False,
        }

    # Use the latest snapshot of the day
    latest = max(snapshots, key=lambda s: s.hour)

    # Calculate top 5 services
    top_services = sorted(
        latest.cost_by_service.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:5]

    # Calculate trend (compare to 7-day average)
    trend = _calculate_trend(storage, latest)

    # Budget info
    budget_percent = 0.0
    budget_monthly = 0.0
    budget_spent = 0.0
    if latest.budget_status:
        budget_percent = latest.budget_status.monthly_percent
        budget_monthly = latest.budget_status.monthly_budget
        budget_spent = latest.budget_status.monthly_spent

    # Forecast
    forecast = 0.0
    if latest.forecast:
        forecast = latest.forecast.end_of_month

    # cost_data_date is the actual date the service costs represent
    # (may differ from snapshot date due to cost_data_lag_days)
    cost_data_date = latest.cost_data_date or target_date

    # Get recent daily costs for the "incomplete data" footnote
    # These are more recent dates that may not be fully populated yet
    recent_daily_costs = _get_recent_daily_costs(storage, cost_data_date)

    # Calculate provider-specific costs (AWS vs Claude)
    provider_costs = _calculate_provider_costs(latest.cost_by_service)

    return {
        "date": target_date,
        "cost_data_date": cost_data_date,  # Actual date the costs represent
        "total_cost": latest.total_cost,
        "top_services": [
            {"service": service, "cost": cost} for service, cost in top_services
        ],
        "trend": trend,
        "budget_percent": budget_percent,
        "budget_monthly": budget_monthly,
        "budget_spent": budget_spent,
        "forecast": forecast,  # AWS-only forecast
        "provider_costs": provider_costs,  # Costs broken down by provider
        "recent_daily_costs": recent_daily_costs,  # For incomplete data footnote
        "has_data": True,
        "used_fallback": used_fallback,
    }


def build_weekly_summary(
    storage: DynamoDBStorage,
    end_date: str | None = None,
) -> dict[str, Any]:
    """
    Build a summary of the previous week's costs.

    Args:
        storage: DynamoDB storage client.
        end_date: End date of the week (YYYY-MM-DD). Defaults to yesterday.

    Returns:
        Dict with: total_cost, week_over_week_change, top_services, anomaly_count, etc.
    """
    if end_date is None:
        end_date = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()

    end_dt = datetime.fromisoformat(end_date).date()
    start_dt = end_dt - timedelta(days=6)  # 7 days including end_date
    start_date = start_dt.isoformat()

    # Collect snapshots for this week
    daily_totals: dict[str, float] = {}
    service_totals: dict[str, float] = {}
    anomaly_count = 0
    latest_budget = None
    latest_forecast = None

    current = start_dt
    while current <= end_dt:
        date_str = current.isoformat()
        snapshots = storage.get_snapshots_for_date(date_str)

        if snapshots:
            # Use latest snapshot of each day
            latest = max(snapshots, key=lambda s: s.hour)
            daily_totals[date_str] = latest.total_cost

            # Accumulate service costs
            for service, cost in latest.cost_by_service.items():
                service_totals[service] = service_totals.get(service, 0) + cost

            # Count anomalies
            anomaly_count += len(latest.anomalies_detected)

            # Keep latest budget/forecast
            if latest.budget_status:
                latest_budget = latest.budget_status
            if latest.forecast:
                latest_forecast = latest.forecast

        current += timedelta(days=1)

    if not daily_totals:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "total_cost": 0.0,
            "week_over_week_change": 0.0,
            "top_services": [],
            "anomaly_count": 0,
            "mtd_cost": 0.0,
            "budget_percent": 0.0,
            "forecast": 0.0,
            "has_data": False,
        }

    # Calculate this week's total
    week_total = sum(daily_totals.values())

    # Get previous week's data for comparison
    prev_week_end = start_dt - timedelta(days=1)
    prev_week_start = prev_week_end - timedelta(days=6)
    prev_week_total = 0.0

    current = prev_week_start
    while current <= prev_week_end:
        date_str = current.isoformat()
        snapshots = storage.get_snapshots_for_date(date_str)
        if snapshots:
            latest = max(snapshots, key=lambda s: s.hour)
            prev_week_total += latest.total_cost
        current += timedelta(days=1)

    # Calculate week-over-week change
    week_over_week_change = 0.0
    if prev_week_total > 0:
        week_over_week_change = ((week_total - prev_week_total) / prev_week_total) * 100

    # Top 5 services for the week
    top_services = sorted(
        service_totals.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:5]

    # Budget info
    budget_percent = 0.0
    mtd_cost = 0.0
    if latest_budget:
        budget_percent = latest_budget.monthly_percent
        mtd_cost = latest_budget.monthly_spent

    # Forecast
    forecast = 0.0
    if latest_forecast:
        forecast = latest_forecast.end_of_month

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_cost": week_total,
        "daily_average": week_total / len(daily_totals) if daily_totals else 0,
        "week_over_week_change": week_over_week_change,
        "top_services": [
            {"service": service, "cost": cost} for service, cost in top_services
        ],
        "anomaly_count": anomaly_count,
        "mtd_cost": mtd_cost,
        "budget_percent": budget_percent,
        "forecast": forecast,
        "has_data": True,
    }


def _calculate_provider_costs(cost_by_service: dict[str, float]) -> dict[str, float]:
    """
    Calculate costs grouped by provider.

    Services are identified by prefix:
    - "Claude::" prefix → Claude/Anthropic API costs
    - Everything else → AWS costs

    Args:
        cost_by_service: Dict of service name to cost.

    Returns:
        Dict with "aws" and "claude" keys containing total costs for each.
    """
    aws_total = 0.0
    claude_total = 0.0

    for service, cost in cost_by_service.items():
        if service.startswith("Claude::"):
            claude_total += cost
        else:
            aws_total += cost

    return {
        "aws": round(aws_total, 2),
        "claude": round(claude_total, 2),
    }


def _get_recent_daily_costs(
    storage: DynamoDBStorage,
    cost_data_date: str,
) -> list[dict[str, Any]]:
    """
    Get daily costs for dates after cost_data_date (incomplete/recent data).

    These are dates more recent than the "accurate" cost_data_date,
    shown as a heads-up that data is still populating.

    Args:
        storage: DynamoDB storage client.
        cost_data_date: The date of accurate cost data (YYYY-MM-DD).

    Returns:
        List of {"date": str, "cost": float} for recent days.
    """
    today = datetime.now(UTC).date()
    cost_date = datetime.fromisoformat(cost_data_date).date()

    recent_costs = []

    # Get days between cost_data_date and today (exclusive of cost_data_date)
    current = cost_date + timedelta(days=1)
    while current <= today:
        date_str = current.isoformat()
        snapshots = storage.get_snapshots_for_date(date_str)

        if snapshots:
            latest = max(snapshots, key=lambda s: s.hour)
            recent_costs.append({
                "date": date_str,
                "cost": latest.total_cost,
            })

        current += timedelta(days=1)

    return recent_costs


def _calculate_trend(storage: DynamoDBStorage, latest: CostSnapshot) -> str:
    """
    Calculate cost trend by comparing to 7-day average.

    Args:
        storage: DynamoDB storage client.
        latest: Latest snapshot.

    Returns:
        Trend string: "increasing", "decreasing", or "stable".
    """
    # Get last 7 days of snapshots
    today = datetime.now(UTC).date()
    daily_costs = []

    for i in range(1, 8):  # Skip today, get last 7 days
        date_str = (today - timedelta(days=i)).isoformat()
        snapshots = storage.get_snapshots_for_date(date_str)
        if snapshots:
            day_snapshot = max(snapshots, key=lambda s: s.hour)
            daily_costs.append(day_snapshot.total_cost)

    if len(daily_costs) < 3:
        return "unknown"

    avg = sum(daily_costs) / len(daily_costs)
    current = latest.total_cost

    # 10% threshold for trend detection
    if current > avg * 1.10:
        return "increasing"
    elif current < avg * 0.90:
        return "decreasing"
    else:
        return "stable"