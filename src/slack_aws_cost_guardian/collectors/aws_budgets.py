"""AWS Budgets collector."""

from __future__ import annotations

from datetime import date, datetime

import boto3
from botocore.exceptions import ClientError

from slack_aws_cost_guardian.collectors.base import BudgetInfo


class BudgetsCollector:
    """
    Collect budget information from AWS Budgets.

    This collector retrieves:
    - Budget limits and current spend
    - Forecasted spend
    - Budget utilization percentage
    """

    collector_name = "budgets"

    def __init__(
        self,
        region: str = "us-east-1",
        budgets_client: boto3.client | None = None,
        sts_client: boto3.client | None = None,
    ):
        """
        Initialize the Budgets collector.

        Args:
            region: AWS region.
            budgets_client: Optional boto3 Budgets client.
            sts_client: Optional boto3 STS client for account ID.
        """
        self.region = region
        self._budgets_client = budgets_client
        self._sts_client = sts_client
        self._account_id: str | None = None

    @property
    def budgets_client(self) -> boto3.client:
        """Get or create Budgets client."""
        if self._budgets_client is None:
            self._budgets_client = boto3.client("budgets", region_name=self.region)
        return self._budgets_client

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

    def collect(self) -> list[BudgetInfo]:
        """
        Collect all budget information.

        Returns:
            List of BudgetInfo objects for each configured budget.
        """
        try:
            response = self.budgets_client.describe_budgets(AccountId=self.account_id)

            budgets = []
            for budget in response.get("Budgets", []):
                budget_info = self._parse_budget(budget)
                if budget_info:
                    budgets.append(budget_info)

            return budgets

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "AccessDeniedException":
                print("No permission to access Budgets API")
            elif error_code == "NotFoundException":
                print("No budgets configured")
            else:
                print(f"Error getting budgets: {e}")
            return []

    def _parse_budget(self, budget: dict) -> BudgetInfo | None:
        """Parse a budget response into BudgetInfo."""
        try:
            budget_name = budget["BudgetName"]
            budget_type = budget.get("BudgetType", "")

            # Only handle COST budgets for now
            if budget_type != "COST":
                return None

            limit = float(budget.get("BudgetLimit", {}).get("Amount", 0))
            calculated_spend = budget.get("CalculatedSpend", {})

            actual_spend = float(
                calculated_spend.get("ActualSpend", {}).get("Amount", 0)
            )
            forecasted_spend = float(
                calculated_spend.get("ForecastedSpend", {}).get("Amount", 0)
            )

            percentage_used = (actual_spend / limit * 100) if limit > 0 else 0

            return BudgetInfo(
                name=budget_name,
                limit=round(limit, 2),
                actual_spend=round(actual_spend, 2),
                forecasted_spend=round(forecasted_spend, 2),
                percentage_used=round(percentage_used, 1),
                currency=budget.get("BudgetLimit", {}).get("Unit", "USD"),
            )

        except (KeyError, ValueError) as e:
            print(f"Error parsing budget: {e}")
            return None

    def get_budget_status(self, budget_name: str) -> BudgetInfo | None:
        """
        Get status for a specific budget.

        Args:
            budget_name: Name of the budget to retrieve.

        Returns:
            BudgetInfo if found, None otherwise.
        """
        try:
            response = self.budgets_client.describe_budget(
                AccountId=self.account_id,
                BudgetName=budget_name,
            )

            return self._parse_budget(response.get("Budget", {}))

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "NotFoundException":
                return None
            print(f"Error getting budget {budget_name}: {e}")
            return None