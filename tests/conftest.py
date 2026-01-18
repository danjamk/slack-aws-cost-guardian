"""Pytest configuration and fixtures."""

import pytest
from datetime import datetime, timedelta


@pytest.fixture
def sample_cost_by_service():
    """Sample cost breakdown by service."""
    return {
        "Amazon EC2": 45.50,
        "Amazon RDS": 32.20,
        "AWS Lambda": 12.30,
        "Amazon S3": 8.15,
        "Amazon CloudWatch": 5.25,
    }


@pytest.fixture
def sample_snapshot_data(sample_cost_by_service):
    """Sample cost snapshot data."""
    return {
        "snapshot_id": "test-snapshot-123",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "account_id": "123456789012",
        "date": datetime.utcnow().date().isoformat(),
        "hour": 12,
        "period_type": "daily",
        "total_cost": sum(sample_cost_by_service.values()),
        "currency": "USD",
        "cost_by_service": sample_cost_by_service,
    }


@pytest.fixture
def sample_config_dict():
    """Sample configuration dictionary."""
    return {
        "project_name": "test-guardian",
        "environment": "dev",
        "aws": {
            "region": "us-east-1",
        },
        "anomaly_detection": {
            "enabled": True,
            "baseline_days": 14,
            "thresholds": {
                "absolute": 100,
                "percent_change": 50,
                "std_deviations": 2.5,
            },
            "filters": {
                "minimum_cost": 5,
                "new_service_minimum": 1,
            },
            "alert_on_new_services": True,
        },
        "slack": {
            "enabled": True,
            "channels": {
                "critical": {
                    "name": "#alerts-critical",
                    "webhook_secret_key": "webhook_url_critical",
                },
                "heartbeat": {
                    "name": "#alerts-general",
                    "webhook_secret_key": "webhook_url_heartbeat",
                },
            },
        },
    }