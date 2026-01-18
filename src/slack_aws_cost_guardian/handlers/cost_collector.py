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
from slack_aws_cost_guardian.analysis.report_builder import build_daily_summary, build_weekly_summary
from slack_aws_cost_guardian.collectors.aws_budgets import BudgetsCollector
from slack_aws_cost_guardian.collectors.aws_cost_explorer import CostExplorerCollector
from slack_aws_cost_guardian.config import load_config, load_guardian_context
from slack_aws_cost_guardian.llm import LLMClient, SYSTEM_PROMPT
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
    - skip_llm: bool - If true, skips AI analysis (faster testing)
    - dry_run: bool - Collect and analyze but don't store or notify
    """
    print(f"Cost collector invoked at {datetime.utcnow().isoformat()}")
    print(f"Event: {json.dumps(event)}")

    # Test mode flags
    test_mode = event.get("test_mode", False)
    force_anomaly = event.get("force_anomaly", False)
    skip_storage = event.get("skip_storage", False) or event.get("dry_run", False)
    skip_slack = event.get("skip_slack", False) or event.get("dry_run", False)
    skip_llm = event.get("skip_llm", False)

    # Report type: "daily", "weekly", or None for normal collection
    report_type = event.get("report_type")

    if report_type in ("daily", "weekly"):
        return _handle_report_generation(
            event=event,
            report_type=report_type,
            test_mode=test_mode,
            skip_slack=skip_slack,
            skip_llm=skip_llm,
        )

    # Backfill mode: load historical data
    backfill_days = event.get("backfill_days")
    if backfill_days:
        return _handle_backfill(
            days=int(backfill_days),
            test_mode=test_mode,
        )

    if test_mode:
        print("=" * 50)
        print("*** RUNNING IN TEST MODE ***")
        print(f"  force_anomaly: {force_anomaly}")
        print(f"  skip_storage: {skip_storage}")
        print(f"  skip_slack: {skip_slack}")
        print(f"  skip_llm: {skip_llm}")
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

    # Load guardian context for AI analysis
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

    # Initialize LLM client (if configured and not skipped)
    llm_client: LLMClient | None = None
    llm_secret_name = os.environ.get("LLM_SECRET_NAME")
    if llm_secret_name and not skip_llm:
        try:
            llm_client = LLMClient(
                config=config.llm,
                secret_name=llm_secret_name,
                region=config.aws.region,
            )
            print(f"LLM client initialized (provider: {config.llm.provider})")
        except Exception as e:
            print(f"Warning: Could not initialize LLM client: {e}")
    elif skip_llm:
        print("[SKIP] LLM analysis disabled")

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

                # Generate AI analysis (graceful degradation if fails)
                ai_analysis = None
                if llm_client:
                    try:
                        # Build context for the LLM
                        historical_summary = _build_historical_summary(historical, anomaly.service)
                        anomaly_data = {
                            "service": anomaly.service,
                            "current_cost": anomaly.current_cost,
                            "baseline_cost": anomaly.baseline_cost,
                            "absolute_change": anomaly.absolute_change,
                            "percent_change": anomaly.percent_change,
                            "severity": anomaly.severity,
                            "is_new_service": anomaly.is_new_service,
                        }

                        ai_analysis = llm_client.analyze_anomaly(
                            anomaly_data=anomaly_data,
                            historical_context=historical_summary,
                            user_context=guardian_context,
                            system_prompt=SYSTEM_PROMPT,
                        )

                        if ai_analysis:
                            print(f"Generated AI analysis for {anomaly.service}")
                    except Exception as e:
                        print(f"AI analysis failed for {anomaly.service}: {e}")

                # Format and send message
                message = slack_formatter.format_anomaly_alert(
                    anomaly=anomaly,
                    alert_id=alert_id,
                    ai_analysis=ai_analysis,
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


def _build_historical_summary(snapshots: list[CostSnapshot], service: str) -> str:
    """
    Build a brief historical context summary for the LLM.

    Args:
        snapshots: List of recent cost snapshots.
        service: AWS service name to summarize.

    Returns:
        A string summary of recent costs for the service.
    """
    if not snapshots:
        return "No historical data available."

    # Get last 7 days of costs for this service
    service_costs = []
    seen_dates = set()

    for s in snapshots:
        if s.date in seen_dates:
            continue
        seen_dates.add(s.date)

        cost = s.cost_by_service.get(service, 0)
        if cost > 0:
            service_costs.append(f"  {s.date}: ${cost:.2f}")

        if len(service_costs) >= 7:
            break

    if not service_costs:
        return f"No recent cost history for {service}."

    return f"Recent {service} daily costs:\n" + "\n".join(service_costs)


def _handle_report_generation(
    event: dict[str, Any],
    report_type: str,
    test_mode: bool,
    skip_slack: bool,
    skip_llm: bool,
) -> dict[str, Any]:
    """
    Handle daily or weekly report generation.

    Args:
        event: Lambda event.
        report_type: "daily" or "weekly".
        test_mode: Whether running in test mode.
        skip_slack: Whether to skip Slack notifications.
        skip_llm: Whether to skip LLM insights.

    Returns:
        Lambda response dict.
    """
    print(f"Generating {report_type} report...")

    # Load configuration
    config = load_config()

    # Get environment variables
    table_name = os.environ.get("TABLE_NAME", f"cost-guardian-{config.environment}")
    config_bucket = os.environ.get("CONFIG_BUCKET", "")
    slack_secret_name = os.environ.get(
        "SLACK_SECRET_NAME", f"cost-guardian/{config.environment}/slack"
    )

    # Initialize storage
    storage = DynamoDBStorage(table_name)
    slack_formatter = SlackFormatter()

    # Load guardian context for AI analysis
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

    # Initialize LLM client (if configured and not skipped)
    llm_client: LLMClient | None = None
    llm_secret_name = os.environ.get("LLM_SECRET_NAME")
    if llm_secret_name and not skip_llm:
        try:
            llm_client = LLMClient(
                config=config.llm,
                secret_name=llm_secret_name,
                region=config.aws.region,
            )
            print(f"LLM client initialized (provider: {config.llm.provider})")
        except Exception as e:
            print(f"Warning: Could not initialize LLM client: {e}")

    # Generate the appropriate summary
    if report_type == "daily":
        summary = build_daily_summary(storage)
        fallback_note = " (fallback to today)" if summary.get("used_fallback") else ""
        print(f"Built daily summary for {summary.get('date')}{fallback_note}: ${summary.get('total_cost', 0):.2f}")
    else:  # weekly
        summary = build_weekly_summary(storage)
        print(
            f"Built weekly summary for {summary.get('start_date')} to {summary.get('end_date')}: "
            f"${summary.get('total_cost', 0):.2f}"
        )

    # Check if we have data
    if not summary.get("has_data"):
        print(f"No data available for {report_type} report")
        return {
            "statusCode": 200,
            "body": {
                "report_type": report_type,
                "status": "no_data",
                "message": f"No cost data available for {report_type} report",
            },
        }

    # Generate AI insight
    ai_insight = None
    if llm_client:
        try:
            if report_type == "daily":
                ai_insight = llm_client.generate_daily_insight(
                    daily_summary=summary,
                    user_context=guardian_context,
                    system_prompt=SYSTEM_PROMPT,
                )
            else:  # weekly
                ai_insight = llm_client.generate_weekly_insight(
                    weekly_summary=summary,
                    user_context=guardian_context,
                    system_prompt=SYSTEM_PROMPT,
                )

            if ai_insight:
                print(f"Generated AI insight for {report_type} report")
        except Exception as e:
            print(f"AI insight generation failed: {e}")

    # Format the Slack message
    if report_type == "daily":
        # For daily, we need to convert to CostData format
        # Use the format_daily_report method which expects CostData
        from slack_aws_cost_guardian.collectors.base import CostData, ForecastInfo
        from slack_aws_cost_guardian.storage.models import BudgetStatus

        top_services_dict = {s["service"]: s["cost"] for s in summary.get("top_services", [])}

        cost_data = CostData(
            start_date=summary.get("date", ""),
            end_date=summary.get("date", ""),
            collection_timestamp=datetime.utcnow().isoformat(),
            account_id="",
            total_cost=summary.get("total_cost", 0),
            cost_by_service=top_services_dict,
            trend=summary.get("trend", "unknown"),
            average_daily_cost=summary.get("total_cost", 0),
        )

        if summary.get("forecast", 0) > 0:
            cost_data.forecast = ForecastInfo(
                forecasted_total=summary.get("forecast", 0),
                current_spend=summary.get("budget_spent", 0),
                days_remaining=0,
                daily_average=0,
                month="",
            )

        budget_status = None
        if summary.get("budget_monthly", 0) > 0:
            budget_status = BudgetStatus(
                monthly_budget=summary.get("budget_monthly", 0),
                monthly_spent=summary.get("budget_spent", 0),
                monthly_percent=summary.get("budget_percent", 0),
            )

        message = slack_formatter.format_daily_report(
            cost_data=cost_data,
            budget_status=budget_status,
            ai_insight=ai_insight,
            report_date=summary.get("date"),
            used_fallback=summary.get("used_fallback", False),
        )
    else:  # weekly
        message = slack_formatter.format_weekly_report(
            weekly_summary=summary,
            ai_insight=ai_insight,
        )

    # Send to Slack heartbeat channel
    notification_sent = False
    if config.slack.enabled and not skip_slack:
        print("Sending report to Slack...")
        try:
            webhook_manager = SlackWebhookManager(
                secret_name=slack_secret_name,
                region=config.aws.region,
            )

            channel_key = config.slack.channels["heartbeat"].webhook_secret_key
            webhook_manager.send_to_channel(channel_key, message)
            notification_sent = True
            print(f"Sent {report_type} report to Slack")

        except Exception as e:
            print(f"Error sending Slack notification: {e}")
            if test_mode:
                import traceback
                traceback.print_exc()
    elif skip_slack:
        print(f"[SKIP] Would send {report_type} report to Slack")

    # Return summary
    body: dict[str, Any] = {
        "report_type": report_type,
        "total_cost": summary.get("total_cost", 0),
        "has_ai_insight": ai_insight is not None,
        "notification_sent": notification_sent,
        "test_mode": test_mode,
    }

    # Add date info based on report type
    if report_type == "daily":
        body["date"] = summary.get("date")
        body["used_fallback"] = summary.get("used_fallback", False)
    else:  # weekly
        body["start_date"] = summary.get("start_date")
        body["end_date"] = summary.get("end_date")

    result = {"statusCode": 200, "body": body}

    print(f"\nCompleted: {json.dumps(result['body'], indent=2)}")
    return result


def _handle_backfill(
    days: int,
    test_mode: bool,
) -> dict[str, Any]:
    """
    Backfill historical cost data from AWS Cost Explorer.

    Queries Cost Explorer for daily costs over the specified period
    and creates snapshots for each day.

    Args:
        days: Number of days to backfill.
        test_mode: Whether running in test mode.

    Returns:
        Lambda response dict.
    """
    print(f"Backfilling {days} days of historical data...")

    # Load configuration
    config = load_config()

    # Get environment variables
    table_name = os.environ.get("TABLE_NAME", f"cost-guardian-{config.environment}")

    # Initialize clients
    storage = DynamoDBStorage(table_name)
    cost_explorer = boto3.client("ce", region_name=config.aws.region)
    sts = boto3.client("sts", region_name=config.aws.region)

    # Get account ID
    account_id = sts.get_caller_identity()["Account"]

    # Calculate date range
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=days)
    end_date = today  # Cost Explorer end date is exclusive

    print(f"Querying Cost Explorer for {start_date} to {end_date}...")

    # Query Cost Explorer for daily costs by service
    try:
        response = cost_explorer.get_cost_and_usage(
            TimePeriod={
                "Start": start_date.isoformat(),
                "End": end_date.isoformat(),
            },
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            GroupBy=[
                {"Type": "DIMENSION", "Key": "SERVICE"},
            ],
        )
    except Exception as e:
        print(f"Error querying Cost Explorer: {e}")
        return {
            "statusCode": 500,
            "body": {"error": str(e)},
        }

    # Process results and create snapshots
    snapshots_created = 0
    snapshots_skipped = 0

    for result in response.get("ResultsByTime", []):
        period_start = result["TimePeriod"]["Start"]

        # Check if we already have data for this date
        existing = storage.get_snapshots_for_date(period_start)
        if existing:
            print(f"  {period_start}: Already has {len(existing)} snapshot(s), skipping")
            snapshots_skipped += 1
            continue

        # Build cost by service dict
        cost_by_service = {}
        total_cost = 0.0

        for group in result.get("Groups", []):
            service_name = group["Keys"][0]
            cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
            if cost > 0.001:  # Skip near-zero costs
                cost_by_service[service_name] = cost
                total_cost += cost

        if total_cost < 0.01:
            print(f"  {period_start}: No significant costs, skipping")
            snapshots_skipped += 1
            continue

        # Calculate TTL (90 days from now for backfilled data)
        ttl_days = 90 if config.environment != "dev" else 30
        ttl = int((datetime.utcnow() + timedelta(days=ttl_days)).timestamp())

        # Create snapshot
        snapshot = CostSnapshot(
            timestamp=f"{period_start}T12:00:00Z",  # Noon UTC
            account_id=account_id,
            date=period_start,
            hour=12,  # Store as noon snapshot
            period_type="daily",
            total_cost=total_cost,
            currency="USD",
            cost_by_service=cost_by_service,
            ttl=ttl,
        )

        storage.put_snapshot(snapshot)
        snapshots_created += 1
        print(f"  {period_start}: ${total_cost:.2f} ({len(cost_by_service)} services)")

    # Summary
    result = {
        "statusCode": 200,
        "body": {
            "action": "backfill",
            "days_requested": days,
            "date_range": f"{start_date} to {end_date}",
            "snapshots_created": snapshots_created,
            "snapshots_skipped": snapshots_skipped,
            "test_mode": test_mode,
        },
    }

    print(f"\nBackfill completed: {json.dumps(result['body'], indent=2)}")
    return result