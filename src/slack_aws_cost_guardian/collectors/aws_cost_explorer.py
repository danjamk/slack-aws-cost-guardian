"""AWS Cost Explorer collector.

Cost Explorer API charges $0.01 per request.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal

import boto3
from botocore.exceptions import ClientError

from slack_aws_cost_guardian.collectors.base import (
    AccountCostData,
    CostCollector,
    CostData,
    DailyCost,
    ForecastInfo,
)


class CostExplorerCollector(CostCollector):
    """
    Collect cost data from AWS Cost Explorer.

    This collector retrieves:
    - Total costs for the period
    - Cost breakdown by service
    - Cost breakdown by linked account (for Organizations)
    - Daily cost trend
    - End-of-month forecast
    """

    collector_name = "cost_explorer"

    def __init__(
        self,
        region: str = "us-east-1",
        granularity: Literal["DAILY", "HOURLY", "MONTHLY"] = "DAILY",
        ce_client: boto3.client | None = None,
        sts_client: boto3.client | None = None,
    ):
        """
        Initialize the Cost Explorer collector.

        Args:
            region: AWS region for the Cost Explorer API.
            granularity: Cost granularity (DAILY recommended for cost efficiency).
            ce_client: Optional boto3 Cost Explorer client.
            sts_client: Optional boto3 STS client for account ID.
        """
        self.region = region
        self.granularity = granularity
        self._ce_client = ce_client
        self._sts_client = sts_client
        self._account_id: str | None = None

    @property
    def ce_client(self) -> boto3.client:
        """Get or create Cost Explorer client."""
        if self._ce_client is None:
            self._ce_client = boto3.client("ce", region_name=self.region)
        return self._ce_client

    @property
    def sts_client(self) -> boto3.client:
        """Get or create STS client."""
        if self._sts_client is None:
            self._sts_client = boto3.client("sts", region_name=self.region)
        return self._sts_client

    @property
    def account_id(self) -> str:
        """Get the current AWS account ID."""
        if self._account_id is None:
            self._account_id = self.sts_client.get_caller_identity()["Account"]
        return self._account_id

    def collect(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        lookback_days: int = 14,
    ) -> CostData:
        """
        Collect cost data from Cost Explorer.

        Args:
            start_date: Start date. Defaults to lookback_days ago.
            end_date: End date. Defaults to today.
            lookback_days: Days to look back if start_date not specified.

        Returns:
            CostData with cost breakdown and trends.
        """
        # Default date range
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=lookback_days)

        collection_timestamp = datetime.now(UTC).isoformat() + "Z"

        # Collect all cost data
        daily_costs = self._get_daily_costs(start_date, end_date)
        cost_by_service = self._get_cost_by_service(start_date, end_date)
        cost_by_account = self._get_cost_by_account(start_date, end_date)
        forecast = self._get_forecast()

        # Calculate totals and trends
        # total_cost should be yesterday's cost (matching cost_by_service)
        # This ensures snapshots represent a single day for proper anomaly detection
        total_cost = sum(cost_by_service.values())

        # average_daily uses the full lookback period for trend context
        lookback_total = sum(dc.cost for dc in daily_costs)
        average_daily = lookback_total / len(daily_costs) if daily_costs else 0
        trend = self._calculate_trend(daily_costs)

        return CostData(
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            collection_timestamp=collection_timestamp,
            account_id=self.account_id,
            total_cost=round(total_cost, 2),
            currency="USD",
            cost_by_service=cost_by_service,
            cost_by_account=cost_by_account,
            daily_costs=daily_costs,
            forecast=forecast,
            trend=trend,
            average_daily_cost=round(average_daily, 2),
        )

    def _get_daily_costs(self, start_date: date, end_date: date) -> list[DailyCost]:
        """Get daily cost breakdown."""
        try:
            response = self.ce_client.get_cost_and_usage(
                TimePeriod={
                    "Start": start_date.isoformat(),
                    "End": end_date.isoformat(),
                },
                Granularity="DAILY",
                Metrics=["UnblendedCost"],
            )

            daily_costs = []
            for result in response.get("ResultsByTime", []):
                cost_date = result["TimePeriod"]["Start"]
                cost = float(result["Total"]["UnblendedCost"]["Amount"])
                daily_costs.append(DailyCost(date=cost_date, cost=round(cost, 2)))

            return daily_costs

        except ClientError as e:
            # Log error but don't fail - return empty list
            print(f"Error getting daily costs: {e}")
            return []

    def _get_cost_by_service(self, start_date: date, end_date: date) -> dict[str, float]:
        """
        Get cost breakdown by AWS service for yesterday only.

        For anomaly detection and daily snapshots, we need consistent single-day
        costs per service. The start_date/end_date params are ignored here;
        we always query yesterday's costs to ensure each snapshot represents
        one day for proper baseline comparison.

        The lookback period (start_date to end_date) is still used for:
        - daily_costs: Historical trend data
        - cost_by_account: Account-level aggregates
        - forecast: End-of-month projections
        """
        # Always query yesterday's costs for service breakdown
        yesterday = date.today() - timedelta(days=1)
        query_start = yesterday
        query_end = date.today()

        try:
            response = self.ce_client.get_cost_and_usage(
                TimePeriod={
                    "Start": query_start.isoformat(),
                    "End": query_end.isoformat(),
                },
                Granularity="DAILY",
                Metrics=["UnblendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )

            cost_by_service: dict[str, float] = {}
            for result in response.get("ResultsByTime", []):
                for group in result.get("Groups", []):
                    service_name = group["Keys"][0]
                    cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    if cost > 0.01:  # Filter out negligible costs
                        cost_by_service[service_name] = round(cost, 2)

            return cost_by_service

        except ClientError as e:
            print(f"Error getting cost by service: {e}")
            return {}

    def _get_cost_by_account(
        self, start_date: date, end_date: date
    ) -> dict[str, AccountCostData]:
        """Get cost breakdown by linked account (for Organizations)."""
        try:
            response = self.ce_client.get_cost_and_usage(
                TimePeriod={
                    "Start": start_date.isoformat(),
                    "End": end_date.isoformat(),
                },
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"}],
            )

            cost_by_account: dict[str, AccountCostData] = {}
            for result in response.get("ResultsByTime", []):
                for group in result.get("Groups", []):
                    account_id = group["Keys"][0]
                    cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    if cost > 0.01:
                        if account_id not in cost_by_account:
                            cost_by_account[account_id] = AccountCostData(
                                account_id=account_id,
                                account_name=account_id,  # Name requires Organizations API
                                total_cost=0,
                            )
                        cost_by_account[account_id].total_cost += round(cost, 2)

            return cost_by_account

        except ClientError as e:
            # This might fail if not using Organizations - that's OK
            if "LINKED_ACCOUNT" in str(e):
                return {}
            print(f"Error getting cost by account: {e}")
            return {}

    def _get_forecast(self) -> ForecastInfo | None:
        """Get end-of-month cost forecast."""
        try:
            now = date.today()
            month_start = now.replace(day=1)
            # Calculate the first day of next month
            if now.month == 12:
                month_end = now.replace(year=now.year + 1, month=1, day=1)
            else:
                month_end = now.replace(month=now.month + 1, day=1)

            # Can't forecast if we're at the end of month
            if now >= month_end - timedelta(days=1):
                return None

            # Get forecast
            forecast_response = self.ce_client.get_cost_forecast(
                TimePeriod={
                    "Start": now.isoformat(),
                    "End": month_end.isoformat(),
                },
                Metric="UNBLENDED_COST",
                Granularity="MONTHLY",
            )

            forecasted_total = float(forecast_response["Total"]["Amount"])

            # Get current month spend
            current_response = self.ce_client.get_cost_and_usage(
                TimePeriod={
                    "Start": month_start.isoformat(),
                    "End": now.isoformat(),
                },
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
            )

            current_spend = 0.0
            if current_response.get("ResultsByTime"):
                current_spend = float(
                    current_response["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"]
                )

            days_elapsed = (now - month_start).days
            days_remaining = (month_end - now).days
            daily_average = current_spend / days_elapsed if days_elapsed > 0 else 0

            return ForecastInfo(
                forecasted_total=round(forecasted_total + current_spend, 2),
                current_spend=round(current_spend, 2),
                days_remaining=days_remaining,
                daily_average=round(daily_average, 2),
                month=month_start.strftime("%Y-%m"),
            )

        except ClientError as e:
            # Forecast might fail with insufficient data
            print(f"Error getting forecast: {e}")
            return None

    def _calculate_trend(self, daily_costs: list[DailyCost]) -> str:
        """
        Calculate cost trend from daily costs.

        Compares first half to second half of the period.
        """
        if len(daily_costs) < 2:
            return "unknown"

        mid = len(daily_costs) // 2
        first_half = sum(dc.cost for dc in daily_costs[:mid])
        second_half = sum(dc.cost for dc in daily_costs[mid:])

        # Normalize for different period lengths
        first_avg = first_half / mid if mid > 0 else 0
        second_avg = second_half / (len(daily_costs) - mid) if (len(daily_costs) - mid) > 0 else 0

        if first_avg == 0:
            return "unknown"

        change_pct = (second_avg - first_avg) / first_avg * 100

        if change_pct > 10:
            return "increasing"
        elif change_pct < -10:
            return "decreasing"
        else:
            return "stable"

    def get_cost_for_date(self, target_date: date) -> dict[str, float]:
        """
        Get cost breakdown for a specific date.

        Useful for getting yesterday's costs for daily reports.
        """
        next_day = target_date + timedelta(days=1)

        try:
            response = self.ce_client.get_cost_and_usage(
                TimePeriod={
                    "Start": target_date.isoformat(),
                    "End": next_day.isoformat(),
                },
                Granularity="DAILY",
                Metrics=["UnblendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )

            cost_by_service: dict[str, float] = {}
            for result in response.get("ResultsByTime", []):
                for group in result.get("Groups", []):
                    service_name = group["Keys"][0]
                    cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    if cost > 0.01:
                        cost_by_service[service_name] = round(cost, 2)

            return cost_by_service

        except ClientError as e:
            print(f"Error getting cost for date: {e}")
            return {}