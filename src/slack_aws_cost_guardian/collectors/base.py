"""Base classes for cost data collectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


@dataclass
class ServiceCost:
    """Cost for a single service."""

    service: str
    cost: float
    currency: str = "USD"


@dataclass
class AccountCostData:
    """Cost data for a linked account."""

    account_id: str
    account_name: str
    total_cost: float
    cost_by_service: dict[str, float] = field(default_factory=dict)


@dataclass
class DailyCost:
    """Cost for a single day."""

    date: str  # YYYY-MM-DD
    cost: float


@dataclass
class BudgetInfo:
    """Budget utilization information."""

    name: str
    limit: float
    actual_spend: float
    forecasted_spend: float
    percentage_used: float
    currency: str = "USD"


@dataclass
class ForecastInfo:
    """Cost forecast information."""

    forecasted_total: float
    current_spend: float
    days_remaining: int
    daily_average: float
    month: str  # YYYY-MM
    currency: str = "USD"


@dataclass
class CostData:
    """
    Collected cost data from AWS.

    This is the intermediate format between AWS APIs and our storage models.
    """

    # Time period
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    collection_timestamp: str  # ISO 8601

    # Account info
    account_id: str

    # Cost summary
    total_cost: float
    currency: str = "USD"

    # Breakdown
    cost_by_service: dict[str, float] = field(default_factory=dict)
    cost_by_account: dict[str, AccountCostData] = field(default_factory=dict)
    daily_costs: list[DailyCost] = field(default_factory=list)

    # Additional context
    budgets: list[BudgetInfo] = field(default_factory=list)
    forecast: ForecastInfo | None = None

    # Trend info
    trend: str = "unknown"  # "increasing", "decreasing", "stable", "unknown"
    average_daily_cost: float = 0.0


class CostCollector(ABC):
    """Abstract base class for cost data collectors."""

    @abstractmethod
    def collect(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> CostData:
        """
        Collect cost data for the specified period.

        Args:
            start_date: Start of the period. Defaults to implementation-specific.
            end_date: End of the period. Defaults to today.

        Returns:
            CostData: Collected cost information.
        """
        pass

    @property
    @abstractmethod
    def collector_name(self) -> str:
        """Return the name of this collector."""
        pass