"""Tests for configuration module."""

import pytest
from slack_aws_cost_guardian.config.schema import (
    Config,
    AnomalyDetectionConfig,
    SlackConfig,
)


class TestConfig:
    """Tests for Config schema."""

    def test_default_config(self):
        """Test that default config is valid."""
        config = Config()
        assert config.project_name == "slack-aws-cost-guardian"
        assert config.environment == "dev"
        assert config.aws.region == "us-east-1"

    def test_config_from_dict(self, sample_config_dict):
        """Test creating config from dictionary."""
        config = Config(**sample_config_dict)
        assert config.project_name == "test-guardian"
        assert config.anomaly_detection.enabled is True
        assert config.anomaly_detection.thresholds.absolute == 100

    def test_anomaly_detection_defaults(self):
        """Test anomaly detection default values."""
        config = AnomalyDetectionConfig()
        assert config.enabled is True
        assert config.baseline_days == 14
        assert config.thresholds.percent_change == 50
        assert config.filters.minimum_cost == 5

    def test_slack_config_defaults(self):
        """Test Slack config default values."""
        config = SlackConfig()
        assert config.enabled is True
        assert "critical" in config.channels
        assert "heartbeat" in config.channels


class TestConfigValidation:
    """Tests for config validation."""

    def test_invalid_baseline_days(self):
        """Test that invalid baseline_days raises error."""
        with pytest.raises(ValueError):
            AnomalyDetectionConfig(baseline_days=0)

    def test_invalid_threshold(self):
        """Test that negative threshold raises error."""
        with pytest.raises(ValueError):
            AnomalyDetectionConfig(
                thresholds={"absolute": -100, "percent_change": 50, "std_deviations": 2.5}
            )