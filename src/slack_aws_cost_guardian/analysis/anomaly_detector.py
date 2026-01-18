"""Anomaly detection for AWS costs."""

from dataclasses import dataclass
from typing import Literal

from slack_aws_cost_guardian.analysis.baseline import Baseline, BaselineCalculator
from slack_aws_cost_guardian.config.schema import AnomalyDetectionConfig
from slack_aws_cost_guardian.storage.models import ChangeLog, CostSnapshot


@dataclass
class DetectedAnomaly:
    """A detected cost anomaly."""

    service: str
    current_cost: float
    baseline_cost: float
    absolute_change: float
    percent_change: float
    std_deviations: float  # How many std devs from mean
    severity: Literal["info", "warning", "critical"]
    reason: str  # Why this was flagged
    is_new_service: bool = False

    @property
    def description(self) -> str:
        """Human-readable description of the anomaly."""
        if self.is_new_service:
            return f"New service {self.service} appeared with ${self.current_cost:.2f} cost"

        direction = "increased" if self.absolute_change > 0 else "decreased"
        return (
            f"{self.service} cost {direction} by ${abs(self.absolute_change):.2f} "
            f"({self.percent_change:+.0f}%) from baseline ${self.baseline_cost:.2f}"
        )


class AnomalyDetector:
    """
    Detect cost anomalies using multiple signals.

    Detection strategies:
    1. Absolute threshold - Cost exceeds configured limit
    2. Percentage change - Cost changed by X% vs baseline
    3. Standard deviation - Cost deviates by N std devs from mean
    4. New service - Service appears that wasn't in previous period
    """

    def __init__(self, config: AnomalyDetectionConfig):
        """
        Initialize the anomaly detector.

        Args:
            config: Anomaly detection configuration.
        """
        self.config = config
        self.baseline_calculator = BaselineCalculator()

    def detect(
        self,
        current_snapshot: CostSnapshot,
        historical_snapshots: list[CostSnapshot],
        active_changes: list[ChangeLog] | None = None,
    ) -> list[DetectedAnomaly]:
        """
        Detect anomalies in the current snapshot.

        Args:
            current_snapshot: The current cost snapshot to analyze.
            historical_snapshots: Historical snapshots for baseline calculation.
            active_changes: List of acknowledged active changes to filter out.

        Returns:
            List of detected anomalies.
        """
        if not self.config.enabled:
            return []

        anomalies: list[DetectedAnomaly] = []

        # Get all services from history
        historical_services = self.baseline_calculator.get_all_services(historical_snapshots)
        current_services = set(current_snapshot.cost_by_service.keys())

        # Check for new services
        if self.config.alert_on_new_services:
            new_service_anomalies = self._detect_new_services(
                current_snapshot, historical_services, current_services
            )
            anomalies.extend(new_service_anomalies)

        # Check each service for anomalies
        for service in current_services:
            if service not in historical_services:
                continue  # Already handled as new service

            current_cost = current_snapshot.cost_by_service.get(service, 0)

            # Skip if below minimum cost threshold
            if current_cost < self.config.filters.minimum_cost:
                continue

            baseline = self.baseline_calculator.calculate_service_baseline(
                historical_snapshots, service
            )

            if not baseline.has_enough_data:
                continue

            anomaly = self._check_service_anomaly(service, current_cost, baseline)
            if anomaly:
                anomalies.append(anomaly)

        # Filter out acknowledged changes
        if active_changes:
            anomalies = self._filter_acknowledged_anomalies(anomalies, active_changes)

        return anomalies

    def _detect_new_services(
        self,
        current_snapshot: CostSnapshot,
        historical_services: set[str],
        current_services: set[str],
    ) -> list[DetectedAnomaly]:
        """Detect new services that weren't in the baseline period."""
        anomalies = []
        new_services = current_services - historical_services

        for service in new_services:
            cost = current_snapshot.cost_by_service.get(service, 0)

            if cost < self.config.filters.new_service_minimum:
                continue

            severity = self._calculate_severity(
                absolute_change=cost,
                percent_change=100,  # 100% new
            )

            anomalies.append(
                DetectedAnomaly(
                    service=service,
                    current_cost=cost,
                    baseline_cost=0,
                    absolute_change=cost,
                    percent_change=100,
                    std_deviations=0,
                    severity=severity,
                    reason="New service detected",
                    is_new_service=True,
                )
            )

        return anomalies

    def _check_service_anomaly(
        self,
        service: str,
        current_cost: float,
        baseline: Baseline,
    ) -> DetectedAnomaly | None:
        """Check if a service's current cost is anomalous."""
        if baseline.mean == 0:
            return None

        absolute_change = current_cost - baseline.mean
        percent_change = (absolute_change / baseline.mean) * 100 if baseline.mean > 0 else 0

        # Calculate standard deviations from mean
        std_deviations = 0.0
        if baseline.std > 0:
            std_deviations = abs(absolute_change) / baseline.std

        # Check each threshold
        reasons = []

        # Absolute threshold
        if abs(absolute_change) >= self.config.thresholds.absolute:
            reasons.append(f"Absolute change ${abs(absolute_change):.2f} >= ${self.config.thresholds.absolute}")

        # Percentage change threshold
        if abs(percent_change) >= self.config.thresholds.percent_change:
            reasons.append(f"Percent change {abs(percent_change):.0f}% >= {self.config.thresholds.percent_change}%")

        # Standard deviation threshold
        if std_deviations >= self.config.thresholds.std_deviations:
            reasons.append(f"{std_deviations:.1f} std devs >= {self.config.thresholds.std_deviations}")

        if not reasons:
            return None

        severity = self._calculate_severity(absolute_change, percent_change)

        return DetectedAnomaly(
            service=service,
            current_cost=round(current_cost, 2),
            baseline_cost=round(baseline.mean, 2),
            absolute_change=round(absolute_change, 2),
            percent_change=round(percent_change, 1),
            std_deviations=round(std_deviations, 1),
            severity=severity,
            reason="; ".join(reasons),
        )

    def _calculate_severity(
        self,
        absolute_change: float,
        percent_change: float,
    ) -> Literal["info", "warning", "critical"]:
        """Calculate anomaly severity based on magnitude."""
        # Critical if very high absolute change or extreme percentage
        if abs(absolute_change) >= self.config.thresholds.absolute * 2:
            return "critical"
        if abs(percent_change) >= self.config.thresholds.percent_change * 2:
            return "critical"

        # Warning if above normal thresholds
        if abs(absolute_change) >= self.config.thresholds.absolute:
            return "warning"
        if abs(percent_change) >= self.config.thresholds.percent_change:
            return "warning"

        return "info"

    def _filter_acknowledged_anomalies(
        self,
        anomalies: list[DetectedAnomaly],
        active_changes: list[ChangeLog],
    ) -> list[DetectedAnomaly]:
        """
        Filter out anomalies for services with acknowledged active changes.

        This reduces false positives when users have already marked a cost
        change as expected.
        """
        acknowledged_services = {change.service for change in active_changes}

        return [
            anomaly
            for anomaly in anomalies
            if anomaly.service not in acknowledged_services
        ]

    def get_anomaly_summary(self, anomalies: list[DetectedAnomaly]) -> str:
        """Generate a summary of detected anomalies."""
        if not anomalies:
            return "No anomalies detected."

        critical = [a for a in anomalies if a.severity == "critical"]
        warning = [a for a in anomalies if a.severity == "warning"]
        info = [a for a in anomalies if a.severity == "info"]

        total_impact = sum(a.absolute_change for a in anomalies)

        parts = [f"Detected {len(anomalies)} anomalies:"]

        if critical:
            parts.append(f"  - {len(critical)} critical")
        if warning:
            parts.append(f"  - {len(warning)} warning")
        if info:
            parts.append(f"  - {len(info)} info")

        parts.append(f"Total impact: ${total_impact:+.2f}")

        return "\n".join(parts)