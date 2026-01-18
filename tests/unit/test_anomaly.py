"""Tests for anomaly detection module."""

import pytest
from datetime import datetime, timedelta

from slack_aws_cost_guardian.analysis.anomaly_detector import AnomalyDetector, DetectedAnomaly
from slack_aws_cost_guardian.analysis.baseline import BaselineCalculator, Baseline
from slack_aws_cost_guardian.config.schema import AnomalyDetectionConfig
from slack_aws_cost_guardian.storage.models import CostSnapshot


def create_snapshot(date_offset: int, cost_by_service: dict[str, float]) -> CostSnapshot:
    """Helper to create a test snapshot."""
    test_date = datetime.utcnow().date() - timedelta(days=date_offset)
    return CostSnapshot(
        account_id="123456789012",
        date=test_date.isoformat(),
        hour=12,
        total_cost=sum(cost_by_service.values()),
        cost_by_service=cost_by_service,
    )


class TestBaselineCalculator:
    """Tests for BaselineCalculator."""

    def test_empty_snapshots(self):
        """Test baseline with no snapshots."""
        calc = BaselineCalculator()
        baseline = calc.calculate_total_baseline([])
        assert baseline.mean == 0
        assert baseline.sample_count == 0

    def test_single_snapshot(self):
        """Test baseline with single snapshot."""
        calc = BaselineCalculator()
        snapshots = [create_snapshot(1, {"EC2": 100.0})]
        baseline = calc.calculate_total_baseline(snapshots)
        assert baseline.mean == 100.0
        assert baseline.sample_count == 1

    def test_weighted_mean(self):
        """Test that recent costs are weighted more heavily."""
        calc = BaselineCalculator(decay_factor=0.5)
        snapshots = [
            create_snapshot(3, {"EC2": 50.0}),   # Oldest, lowest weight
            create_snapshot(2, {"EC2": 50.0}),
            create_snapshot(1, {"EC2": 100.0}),  # Newest, highest weight
        ]
        baseline = calc.calculate_total_baseline(snapshots)
        # With decay_factor=0.5, recent values should pull mean up
        assert baseline.mean > 60  # Should be higher than simple average of 66.67

    def test_service_baseline(self):
        """Test baseline for specific service."""
        calc = BaselineCalculator()
        snapshots = [
            create_snapshot(2, {"EC2": 100.0, "RDS": 50.0}),
            create_snapshot(1, {"EC2": 120.0, "RDS": 55.0}),
        ]
        ec2_baseline = calc.calculate_service_baseline(snapshots, "EC2")
        rds_baseline = calc.calculate_service_baseline(snapshots, "RDS")

        assert ec2_baseline.mean > 100  # Weighted toward 120
        assert rds_baseline.mean > 50   # Weighted toward 55


class TestAnomalyDetector:
    """Tests for AnomalyDetector."""

    @pytest.fixture
    def detector(self):
        """Create a detector with test config."""
        config = AnomalyDetectionConfig(
            thresholds={"absolute": 50, "percent_change": 30, "std_deviations": 2.0},
            filters={"minimum_cost": 5, "new_service_minimum": 1},
            alert_on_new_services=True,
        )
        return AnomalyDetector(config)

    def test_no_anomalies_stable_costs(self, detector):
        """Test that stable costs don't trigger anomalies."""
        historical = [
            create_snapshot(i, {"EC2": 100.0, "RDS": 50.0})
            for i in range(14, 0, -1)
        ]
        current = create_snapshot(0, {"EC2": 105.0, "RDS": 52.0})

        anomalies = detector.detect(current, historical)
        assert len(anomalies) == 0

    def test_detect_large_increase(self, detector):
        """Test detection of large cost increase."""
        historical = [
            create_snapshot(i, {"EC2": 100.0})
            for i in range(14, 0, -1)
        ]
        current = create_snapshot(0, {"EC2": 200.0})  # 100% increase

        anomalies = detector.detect(current, historical)
        assert len(anomalies) == 1
        assert anomalies[0].service == "EC2"
        assert anomalies[0].percent_change > 90

    def test_detect_new_service(self, detector):
        """Test detection of new service."""
        historical = [
            create_snapshot(i, {"EC2": 100.0})
            for i in range(14, 0, -1)
        ]
        current = create_snapshot(0, {"EC2": 100.0, "SageMaker": 50.0})

        anomalies = detector.detect(current, historical)
        new_service_anomalies = [a for a in anomalies if a.is_new_service]
        assert len(new_service_anomalies) == 1
        assert new_service_anomalies[0].service == "SageMaker"

    def test_ignore_small_costs(self, detector):
        """Test that small costs are ignored."""
        historical = [
            create_snapshot(i, {"EC2": 100.0, "Route53": 0.50})
            for i in range(14, 0, -1)
        ]
        current = create_snapshot(0, {"EC2": 100.0, "Route53": 2.0})  # 300% increase but tiny

        anomalies = detector.detect(current, historical)
        route53_anomalies = [a for a in anomalies if a.service == "Route53"]
        assert len(route53_anomalies) == 0  # Below minimum_cost threshold

    def test_severity_levels(self, detector):
        """Test anomaly severity assignment."""
        historical = [
            create_snapshot(i, {"EC2": 100.0})
            for i in range(14, 0, -1)
        ]

        # Critical: 2x the threshold
        current = create_snapshot(0, {"EC2": 300.0})  # 200% increase
        anomalies = detector.detect(current, historical)
        assert anomalies[0].severity == "critical"


class TestDetectedAnomaly:
    """Tests for DetectedAnomaly dataclass."""

    def test_description_increase(self):
        """Test description for cost increase."""
        anomaly = DetectedAnomaly(
            service="EC2",
            current_cost=150.0,
            baseline_cost=100.0,
            absolute_change=50.0,
            percent_change=50.0,
            std_deviations=2.5,
            severity="warning",
            reason="Test",
        )
        assert "increased" in anomaly.description
        assert "$50.00" in anomaly.description

    def test_description_new_service(self):
        """Test description for new service."""
        anomaly = DetectedAnomaly(
            service="SageMaker",
            current_cost=100.0,
            baseline_cost=0,
            absolute_change=100.0,
            percent_change=100.0,
            std_deviations=0,
            severity="warning",
            reason="New service",
            is_new_service=True,
        )
        assert "New service" in anomaly.description
        assert "SageMaker" in anomaly.description