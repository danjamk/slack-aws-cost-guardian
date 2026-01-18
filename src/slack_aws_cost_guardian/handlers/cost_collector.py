"""
Cost Collector Lambda Handler.

This Lambda is triggered by EventBridge on a schedule to:
1. Collect cost data from AWS Cost Explorer
2. Store snapshots in DynamoDB
3. Detect anomalies
4. Send notifications for anomalies via Slack
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import boto3

from slack_aws_cost_guardian.analysis.anomaly_detector import AnomalyDetector, DetectedAnomaly
from slack_aws_cost_guardian.collectors.aws_budgets import BudgetsCollector
from slack_aws_cost_guardian.collectors.aws_cost_explorer import CostExplorerCollector
from slack_aws_cost_guardian.config import load_config, load_guardian_context
from slack_aws_cost_guardian.notifications.slack.formatter import SlackFormatter
from slack_aws_cost_guardian.notifications.slack.webhook import SlackWebhookManager
from slack_aws_cost_guardian.storage.dynamodb import DynamoDBStorage
from slack_aws_cost_guardian.storage.models import (
    AnomalyInfo,
    BudgetStatus,
    CostForecast,
    CostSnapshot,
)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda handler for cost collection.

    Environment variables:
    - TABLE_NAME: DynamoDB table name
    - CONFIG_BUCKET: S3 bucket for configuration
    - SLACK_SECRET_NAME: Secrets Manager secret for Slack webhooks
    - CONFIG_ENV: Environment (dev, staging, prod)

    Event parameters (for testing):
    - test_mode: bool - If true, runs in test mode with verbose output
    - force_anomaly: bool - If true, generates a fake anomaly for testing Slack
    - skip_storage: bool - If true, doesn't write to DynamoDB
    - skip_slack: bool - If true, doesn't send Slack notifications
    - dry_run: bool - Collect and analyze but don't store or notify
    """
    print(f"Cost collector invoked at {datetime.utcnow().isoformat()}")
    print(f"Event: {json.dumps(event)}")

    # Test mode flags
    test_mode = event.get("test_mode", False)
    force_anomaly = event.get("force_anomaly", False)
    skip_storage = event.get("skip_storage", False) or event.get("dry_run", False)
    skip_slack = event.get("skip_slack", False) or event.get("dry_run", False)

    if test_mode:
        print("=" * 50)
        print("*** RUNNING IN TEST MODE ***")
        print(f"  force_anomaly: {force_anomaly}")
        print(f"  skip_storage: {skip_storage}")
        print(f"  skip_slack: {skip_slack}")
        print("=" * 50)

    # Load configuration
    config = load_config()

    # Get environment variables
    table_name = os.environ.get("TABLE_NAME", f"cost-guardian-{config.environment}")
    config_bucket = os.environ.get("CONFIG_BUCKET", "")
    slack_secret_name = os.environ.get(
        "SLACK_SECRET_NAME", f"cost-guardian/{config.environment}/slack"
    )

    # Initialize clients
    storage = DynamoDBStorage(table_name)
    cost_explorer = CostExplorerCollector(region=config.aws.region)
    budgets_collector = BudgetsCollector(region=config.aws.region)
    anomaly_detector = AnomalyDetector(config.anomaly_detection)
    slack_formatter = SlackFormatter()

    # Load guardian context (for future AI integration)
    guardian_context = ""
    if config_bucket:
        try:
            guardian_context = load_guardian_context(
                bucket_name=config_bucket,
                s3_key=config.guardian_context.s3_key,
            )
            print(f"Loaded guardian context: {len(guardian_context)} chars")
        except Exception as e:
            print(f"Warning: Could not load guardian context: {e}")

    # Collect cost data
    print("Collecting cost data from AWS Cost Explorer...")
    cost_data = cost_explorer.collect(
        lookback_days=config.collection.sources.cost_explorer.lookback_days
    )
    print(f"Collected costs: ${cost_data.total_cost:.2f} total across {len(cost_data.cost_by_service)} services")

    if test_mode:
        print("\nTop 5 services by cost:")
        sorted_services = sorted(cost_data.cost_by_service.items(), key=lambda x: x[1], reverse=True)
        for service, cost in sorted_services[:5]:
            print(f"  - {service}: ${cost:.2f}")

    # Collect budget information
    budget_info = None
    if config.collection.sources.budgets.enabled:
        print("Collecting budget information...")
        budgets = budgets_collector.collect()
        if budgets:
            # Use first budget for now (can be enhanced to support multiple)
            b = budgets[0]
            budget_info = BudgetStatus(
                monthly_budget=b.limit,
                monthly_spent=b.actual_spend,
                monthly_percent=b.percentage_used,
            )
            print(f"Budget status: {b.percentage_used:.1f}% used (${b.actual_spend:.2f} of ${b.limit:.2f})")
        else:
            print("No budgets found")

    # Create snapshot
    print("Creating cost snapshot...")
    snapshot = _create_snapshot(cost_data, budget_info, config.environment)

    # Store snapshot (unless skip_storage)
    if not skip_storage:
        storage.put_snapshot(snapshot)
        print(f"Stored snapshot: {snapshot.snapshot_id}")
    else:
        print(f"[SKIP] Would store snapshot: {snapshot.snapshot_id}")

    # Get historical snapshots for anomaly detection
    print("Loading historical data for baseline...")
    historical = storage.get_recent_snapshots(
        days=config.anomaly_detection.baseline_days,
        account_id=cost_data.account_id,
    )
    print(f"Loaded {len(historical)} historical snapshots")

    # Get active changes to filter acknowledged anomalies
    active_changes = storage.get_active_changes()
    print(f"Found {len(active_changes)} active acknowledged changes")

    # Detect anomalies
    print("Running anomaly detection...")
    anomalies = anomaly_detector.detect(snapshot, historical, active_changes)
    print(f"Detected {len(anomalies)} anomalies")

    # Force a test anomaly if requested
    if force_anomaly:
        test_anomaly = _create_test_anomaly(cost_data)
        anomalies.append(test_anomaly)
        print(f"[TEST] Injected fake anomaly: {test_anomaly.description}")

    if test_mode and anomalies:
        print("\nDetected anomalies:")
        for a in anomalies:
            print(f"  - [{a.severity.upper()}] {a.description}")

    # Update snapshot with anomalies (unless skip_storage)
    if anomalies and not skip_storage:
        snapshot.anomalies_detected = [
            AnomalyInfo(
                service=a.service,
                amount=a.absolute_change,
                percent_change=a.percent_change,
                severity=a.severity,
                baseline_cost=a.baseline_cost,
            )
            for a in anomalies
        ]
        storage.put_snapshot(snapshot)

    # Send Slack notifications for anomalies
    notifications_sent = 0
    if config.slack.enabled and anomalies and not skip_slack:
        print("Sending Slack notifications...")
        try:
            webhook_manager = SlackWebhookManager(
                secret_name=slack_secret_name,
                region=config.aws.region,
            )

            for anomaly in anomalies:
                # Determine which channel to use based on severity
                channel_key = (
                    config.slack.channels["critical"].webhook_secret_key
                    if anomaly.severity == "critical"
                    else config.slack.channels["heartbeat"].webhook_secret_key
                )

                # Generate alert ID
                alert_id = str(uuid4())

                # Format and send message
                message = slack_formatter.format_anomaly_alert(
                    anomaly=anomaly,
                    alert_id=alert_id,
                    ai_analysis=None,  # Phase 2: Add AI analysis
                )

                webhook_manager.send_to_channel(channel_key, message)
                notifications_sent += 1
                print(f"Sent alert for {anomaly.service}: {anomaly.description}")

        except Exception as e:
            print(f"Error sending Slack notifications: {e}")
            if test_mode:
                import traceback
                traceback.print_exc()
    elif anomalies and skip_slack:
        print(f"[SKIP] Would send {len(anomalies)} Slack notifications")

    # Return summary
    result = {
        "statusCode": 200,
        "body": {
            "snapshot_id": snapshot.snapshot_id,
            "total_cost": cost_data.total_cost,
            "services_count": len(cost_data.cost_by_service),
            "anomalies_detected": len(anomalies),
            "notifications_sent": notifications_sent,
            "timestamp": snapshot.timestamp,
            "test_mode": test_mode,
        },
    }

    print(f"\nCompleted: {json.dumps(result['body'], indent=2)}")
    return result


def _create_snapshot(
    cost_data: Any,
    budget_info: BudgetStatus | None,
    environment: str,
) -> CostSnapshot:
    """Create a CostSnapshot from collected data."""
    now = datetime.utcnow()

    # Calculate TTL based on environment (90 days for daily snapshots)
    ttl_days = 90 if environment != "dev" else 7
    ttl = int((now + timedelta(days=ttl_days)).timestamp())

    # Create forecast info if available
    forecast = None
    if cost_data.forecast:
        forecast = CostForecast(
            end_of_month=cost_data.forecast.forecasted_total,
            confidence="medium",
        )

    return CostSnapshot(
        timestamp=now.isoformat() + "Z",
        account_id=cost_data.account_id,
        date=now.date().isoformat(),
        hour=now.hour,
        period_type="daily",
        total_cost=cost_data.total_cost,
        currency=cost_data.currency,
        cost_by_service=cost_data.cost_by_service,
        budget_status=budget_info,
        forecast=forecast,
        ttl=ttl,
    )


def _create_test_anomaly(cost_data: Any) -> DetectedAnomaly:
    """Create a fake anomaly for testing Slack notifications."""
    # Pick the top service or use a default
    if cost_data.cost_by_service:
        top_service = max(cost_data.cost_by_service.items(), key=lambda x: x[1])
        service_name = top_service[0]
        current_cost = top_service[1]
    else:
        service_name = "Amazon EC2"
        current_cost = 100.0

    # Create a fake anomaly showing a 75% increase
    baseline_cost = current_cost / 1.75
    absolute_change = current_cost - baseline_cost

    return DetectedAnomaly(
        service=f"[TEST] {service_name}",
        current_cost=round(current_cost, 2),
        baseline_cost=round(baseline_cost, 2),
        absolute_change=round(absolute_change, 2),
        percent_change=75.0,
        std_deviations=3.0,
        severity="warning",
        reason="[TEST] Forced anomaly for testing Slack notifications",
        is_new_service=False,
    )