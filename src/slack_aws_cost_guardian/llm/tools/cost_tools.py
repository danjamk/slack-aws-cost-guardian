"""Cost query tool implementations."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from slack_aws_cost_guardian.collectors.aws_cost_explorer import CostExplorerCollector
from slack_aws_cost_guardian.llm.tools.registry import ToolRegistry
from slack_aws_cost_guardian.storage.dynamodb import DynamoDBStorage


def _parse_date(date_str: str) -> date:
    """
    Parse a date string into a date object.

    Supports:
    - YYYY-MM-DD format
    - 'today'
    - 'yesterday'
    - 'N_days_ago' (e.g., '7_days_ago')
    """
    date_str = date_str.lower().strip()
    today = date.today()

    if date_str == "today":
        return today
    elif date_str == "yesterday":
        return today - timedelta(days=1)
    elif date_str.endswith("_days_ago"):
        try:
            days = int(date_str.split("_")[0])
            return today - timedelta(days=days)
        except (ValueError, IndexError):
            pass

    # Try parsing as YYYY-MM-DD
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str}. Use YYYY-MM-DD, 'today', 'yesterday', or 'N_days_ago'")


def create_cost_tools(
    table_name: str | None = None,
    region: str = "us-east-1",
) -> ToolRegistry:
    """
    Create a ToolRegistry with cost query tools.

    Args:
        table_name: DynamoDB table name for cached data. If None, uses Cost Explorer only.
        region: AWS region for Cost Explorer.

    Returns:
        ToolRegistry with registered cost tools.
    """
    registry = ToolRegistry()
    collector = CostExplorerCollector(region=region)
    storage = DynamoDBStorage(table_name) if table_name else None

    def get_daily_costs(
        start_date: str,
        end_date: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """Get cost summary for a specific date or date range."""
        try:
            start = _parse_date(start_date)
            end = _parse_date(end_date) if end_date else start

            # For single day queries, try DynamoDB cache first
            if storage and start == end:
                snapshots = storage.get_snapshots_for_date(start.isoformat())
                if account_id:
                    snapshots = [s for s in snapshots if s.account_id == account_id]

                if snapshots:
                    # Use the latest snapshot for the day
                    snapshot = max(snapshots, key=lambda s: s.hour)
                    return {
                        "date": snapshot.date,
                        "total_cost": snapshot.total_cost,
                        "currency": "USD",
                        "by_service": snapshot.cost_by_service,
                        "source": "cache",
                    }

            # Fall back to Cost Explorer
            # Add 1 day to end_date for Cost Explorer API (exclusive end)
            cost_data = collector.collect(
                start_date=start,
                end_date=end + timedelta(days=1),
                lookback_days=1,
            )

            # Build response
            result: dict[str, Any] = {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "total_cost": cost_data.total_cost,
                "currency": cost_data.currency,
                "by_service": cost_data.cost_by_service,
                "source": "cost_explorer",
            }

            if cost_data.daily_costs:
                result["daily_breakdown"] = [
                    {"date": dc.date, "cost": dc.cost}
                    for dc in cost_data.daily_costs
                ]

            return result

        except Exception as e:
            return {"error": str(e)}

    def get_service_trend(
        service: str,
        period: str,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """Get cost trend for a specific AWS service over time."""
        try:
            # Parse period
            period_days = {"7d": 7, "14d": 14, "30d": 30}.get(period, 7)
            end = date.today()
            start = end - timedelta(days=period_days)

            # Collect cost data
            cost_data = collector.collect(
                start_date=start,
                end_date=end,
                lookback_days=period_days,
            )

            # Try to get service-specific daily data from DynamoDB cache
            service_daily: list[dict[str, Any]] = []

            if storage:
                for i in range(period_days):
                    query_date = (end - timedelta(days=i)).isoformat()
                    snapshots = storage.get_snapshots_for_date(query_date)

                    if account_id:
                        snapshots = [s for s in snapshots if s.account_id == account_id]

                    if snapshots:
                        snapshot = max(snapshots, key=lambda s: s.hour)
                        service_cost = snapshot.cost_by_service.get(service, 0.0)
                        service_daily.append({
                            "date": query_date,
                            "cost": service_cost,
                        })

                service_daily.reverse()  # Oldest first

            # Calculate trend if we have data
            trend = "unknown"
            if len(service_daily) >= 2:
                mid = len(service_daily) // 2
                first_half_avg = sum(d["cost"] for d in service_daily[:mid]) / mid
                second_half_avg = sum(d["cost"] for d in service_daily[mid:]) / (len(service_daily) - mid)

                if first_half_avg > 0:
                    change_pct = (second_half_avg - first_half_avg) / first_half_avg * 100
                    if change_pct > 10:
                        trend = f"increasing (+{change_pct:.1f}%)"
                    elif change_pct < -10:
                        trend = f"decreasing ({change_pct:.1f}%)"
                    else:
                        trend = f"stable ({change_pct:+.1f}%)"

            # Get current service cost from latest data
            current_cost = cost_data.cost_by_service.get(service, 0.0)

            return {
                "service": service,
                "period": period,
                "current_daily_cost": current_cost,
                "trend": trend,
                "daily_data": service_daily if service_daily else None,
                "available_services": list(cost_data.cost_by_service.keys())[:10] if not service_daily else None,
            }

        except Exception as e:
            return {"error": str(e)}

    def get_account_breakdown(
        start_date: str,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Get cost breakdown by AWS account for a date range."""
        try:
            start = _parse_date(start_date)
            end = _parse_date(end_date) if end_date else start

            cost_data = collector.collect(
                start_date=start,
                end_date=end + timedelta(days=1),
                lookback_days=(end - start).days + 1,
            )

            accounts = [
                {
                    "account_id": acc_id,
                    "account_name": acc_data.account_name,
                    "total_cost": acc_data.total_cost,
                }
                for acc_id, acc_data in cost_data.cost_by_account.items()
            ]

            # Sort by cost descending
            accounts.sort(key=lambda x: x["total_cost"], reverse=True)

            return {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "total_cost": cost_data.total_cost,
                "currency": "USD",
                "accounts": accounts,
                "account_count": len(accounts),
            }

        except Exception as e:
            return {"error": str(e)}

    def get_top_services(
        start_date: str,
        end_date: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Get the top N services by cost for a date range."""
        try:
            start = _parse_date(start_date)
            end = _parse_date(end_date) if end_date else start

            # Cap limit at 20
            limit = min(limit, 20)

            # Try DynamoDB cache first for single day
            if storage and start == end:
                snapshots = storage.get_snapshots_for_date(start.isoformat())
                if snapshots:
                    snapshot = max(snapshots, key=lambda s: s.hour)
                    services = [
                        {"service": svc, "cost": cost}
                        for svc, cost in sorted(
                            snapshot.cost_by_service.items(),
                            key=lambda x: x[1],
                            reverse=True,
                        )[:limit]
                    ]

                    return {
                        "date": start.isoformat(),
                        "total_cost": snapshot.total_cost,
                        "currency": "USD",
                        "top_services": services,
                        "source": "cache",
                    }

            # Fall back to Cost Explorer
            cost_data = collector.collect(
                start_date=start,
                end_date=end + timedelta(days=1),
                lookback_days=1,
            )

            services = [
                {"service": svc, "cost": cost}
                for svc, cost in sorted(
                    cost_data.cost_by_service.items(),
                    key=lambda x: x[1],
                    reverse=True,
                )[:limit]
            ]

            return {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "total_cost": cost_data.total_cost,
                "currency": "USD",
                "top_services": services,
                "source": "cost_explorer",
            }

        except Exception as e:
            return {"error": str(e)}

    # Register all tools
    registry.register("get_daily_costs", get_daily_costs)
    registry.register("get_service_trend", get_service_trend)
    registry.register("get_account_breakdown", get_account_breakdown)
    registry.register("get_top_services", get_top_services)

    return registry