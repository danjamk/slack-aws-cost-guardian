"""Baseline calculation for anomaly detection."""

import statistics
from dataclasses import dataclass

from slack_aws_cost_guardian.storage.models import CostSnapshot


@dataclass
class Baseline:
    """Baseline statistics for a service or total cost."""

    mean: float
    std: float
    trend: float  # Positive = increasing, negative = decreasing
    min_cost: float
    max_cost: float
    sample_count: int

    @property
    def has_enough_data(self) -> bool:
        """Check if we have enough data for reliable anomaly detection."""
        return self.sample_count >= 3


class BaselineCalculator:
    """
    Calculate baselines for anomaly detection.

    Uses weighted averages where recent days are weighted more heavily,
    allowing faster adaptation to legitimate cost changes.
    """

    def __init__(self, decay_factor: float = 0.9):
        """
        Initialize the baseline calculator.

        Args:
            decay_factor: Exponential decay factor for weighting.
                         0.9 means each day is 90% the weight of the next.
        """
        self.decay_factor = decay_factor

    def calculate_total_baseline(self, snapshots: list[CostSnapshot]) -> Baseline:
        """
        Calculate baseline for total cost.

        Args:
            snapshots: Historical cost snapshots, ordered oldest to newest.

        Returns:
            Baseline statistics for total cost.
        """
        if not snapshots:
            return Baseline(
                mean=0, std=0, trend=0, min_cost=0, max_cost=0, sample_count=0
            )

        costs = [s.total_cost for s in snapshots]
        return self._calculate_baseline(costs)

    def calculate_service_baseline(
        self, snapshots: list[CostSnapshot], service: str
    ) -> Baseline:
        """
        Calculate baseline for a specific service.

        Args:
            snapshots: Historical cost snapshots, ordered oldest to newest.
            service: AWS service name.

        Returns:
            Baseline statistics for the service.
        """
        if not snapshots:
            return Baseline(
                mean=0, std=0, trend=0, min_cost=0, max_cost=0, sample_count=0
            )

        costs = [s.cost_by_service.get(service, 0) for s in snapshots]
        return self._calculate_baseline(costs)

    def _calculate_baseline(self, costs: list[float]) -> Baseline:
        """Calculate baseline from a list of costs."""
        if not costs:
            return Baseline(
                mean=0, std=0, trend=0, min_cost=0, max_cost=0, sample_count=0
            )

        # Filter out zero costs for baseline (service may not have been used)
        non_zero_costs = [c for c in costs if c > 0]
        if not non_zero_costs:
            return Baseline(
                mean=0, std=0, trend=0, min_cost=0, max_cost=0, sample_count=len(costs)
            )

        # Weighted mean (exponential decay, recent = higher weight)
        weights = [self.decay_factor**i for i in range(len(non_zero_costs))]
        weights.reverse()  # Most recent gets highest weight

        weighted_sum = sum(c * w for c, w in zip(non_zero_costs, weights))
        weight_total = sum(weights)
        weighted_mean = weighted_sum / weight_total if weight_total > 0 else 0

        # Standard deviation (unweighted for simplicity)
        std = statistics.stdev(non_zero_costs) if len(non_zero_costs) > 1 else 0

        # Trend (simple linear regression slope)
        trend = self._calculate_trend(non_zero_costs)

        return Baseline(
            mean=round(weighted_mean, 2),
            std=round(std, 2),
            trend=round(trend, 4),
            min_cost=round(min(non_zero_costs), 2),
            max_cost=round(max(non_zero_costs), 2),
            sample_count=len(costs),
        )

    def _calculate_trend(self, costs: list[float]) -> float:
        """
        Calculate trend using simple linear regression.

        Returns the slope of the regression line.
        """
        if len(costs) < 3:
            return 0

        n = len(costs)
        x = list(range(n))
        x_mean = sum(x) / n
        y_mean = sum(costs) / n

        numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, costs))
        denominator = sum((xi - x_mean) ** 2 for xi in x)

        if denominator == 0:
            return 0

        return numerator / denominator

    def get_all_services(self, snapshots: list[CostSnapshot]) -> set[str]:
        """Get all services that appear in the snapshots."""
        services: set[str] = set()
        for snapshot in snapshots:
            services.update(snapshot.cost_by_service.keys())
        return services