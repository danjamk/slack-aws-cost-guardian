#!/usr/bin/env python3
"""CDK application entry point for Slack AWS Cost Guardian."""

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import aws_cdk as cdk
import yaml

from cdk.stacks.storage_stack import StorageStack
from cdk.stacks.collector_stack import CollectorStack
from cdk.stacks.callback_stack import CallbackStack
from cdk.stacks.events_stack import EventsStack


def _get_version() -> str:
    """Read version from VERSION file."""
    version_file = Path(__file__).parent.parent / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    return "0.0.0"


def _get_git_commit() -> str:
    """Get short git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _load_config(environment: str) -> dict:
    """Load configuration from config.yaml and environment-specific overrides."""
    config_dir = Path(__file__).parent.parent / "config"
    config_data: dict = {}

    # Load base config
    base_config = config_dir / "config.yaml"
    if base_config.exists():
        with open(base_config) as f:
            config_data = yaml.safe_load(f) or {}

    # Load environment-specific overrides
    env_config = config_dir / f"config.{environment}.yaml"
    if env_config.exists():
        with open(env_config) as f:
            env_data = yaml.safe_load(f) or {}
            # Deep merge
            for key, value in env_data.items():
                if key in config_data and isinstance(config_data[key], dict) and isinstance(value, dict):
                    config_data[key] = {**config_data[key], **value}
                else:
                    config_data[key] = value

    return config_data


def main():
    """Create and synthesize the CDK application."""
    app = cdk.App()

    # Get version and build info
    version = _get_version()
    git_commit = _get_git_commit()
    deploy_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Get environment from context or environment variable
    environment = app.node.try_get_context("environment") or os.environ.get(
        "CONFIG_ENV", "dev"
    )

    # Load configuration from config.yaml
    config = _load_config(environment)

    # Extract schedule settings from config
    collection_config = config.get("collection", {})
    schedule_config = collection_config.get("schedule", {})
    frequency = schedule_config.get("frequency", "daily")
    schedule_hours = schedule_config.get("hours", [6])

    # Map frequency to schedule hours if not explicitly set
    if frequency == "daily" and schedule_hours == [6]:
        schedule_hours = [6]  # Default: 6 AM UTC
    elif frequency == "2x_daily" and len(schedule_hours) < 2:
        schedule_hours = [6, 18]
    elif frequency == "4x_daily" and len(schedule_hours) < 4:
        schedule_hours = [0, 6, 12, 18]

    # Extract report schedule settings
    reports_config = config.get("reports", {})
    daily_report_hour = reports_config.get("daily", {}).get("schedule_hour", 14)
    weekly_report_hour = reports_config.get("weekly", {}).get("schedule_hour", 14)

    # Check if Anthropic cost collection is enabled
    anthropic_costs_enabled = os.environ.get("ANTHROPIC_COSTS_ENABLED", "").lower() in (
        "true", "1", "yes"
    ) or collection_config.get("sources", {}).get("anthropic", {}).get("enabled", False)

    # Get AWS environment
    aws_env = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    )

    # Common tags for all resources
    tags = {
        "Project": "slack-aws-cost-guardian",
        "Environment": environment,
        "ManagedBy": "CDK",
    }

    # Create the storage stack
    storage_stack = StorageStack(
        app,
        f"CostGuardianStorage-{environment}",
        deploy_env=environment,
        env=aws_env,
    )

    # Apply tags to storage stack
    for key, value in tags.items():
        cdk.Tags.of(storage_stack).add(key, value)

    # Create the collector stack
    # Schedule hours and report times are read from config/config.yaml
    collector_stack = CollectorStack(
        app,
        f"CostGuardianCollector-{environment}",
        environment=environment,
        table=storage_stack.table,
        config_bucket=storage_stack.config_bucket,
        schedule_hours=schedule_hours,
        daily_report_hour_utc=daily_report_hour,
        weekly_report_hour_utc=weekly_report_hour,
        anthropic_costs_enabled=anthropic_costs_enabled,
        version=version,
        git_commit=git_commit,
        deploy_timestamp=deploy_timestamp,
        env=aws_env,
    )

    # Collector depends on storage
    collector_stack.add_dependency(storage_stack)

    # Apply tags to collector stack
    for key, value in tags.items():
        cdk.Tags.of(collector_stack).add(key, value)

    # Output useful values
    cdk.CfnOutput(
        storage_stack,
        "TableName",
        value=storage_stack.table_name,
        description="DynamoDB table name",
    )

    cdk.CfnOutput(
        storage_stack,
        "ConfigBucketName",
        value=storage_stack.config_bucket_name,
        description="S3 bucket for configuration",
    )

    cdk.CfnOutput(
        collector_stack,
        "CollectorFunctionArn",
        value=collector_stack.function_arn,
        description="Cost Collector Lambda ARN",
    )

    cdk.CfnOutput(
        collector_stack,
        "ConfigSecretArn",
        value=collector_stack.config_secret_arn,
        description="Secrets Manager ARN for all configuration (populated from .env during deploy)",
    )

    # Create the callback stack for Slack button handling
    # Note: Callback imports config secret by name, no cross-stack dependency
    callback_stack = CallbackStack(
        app,
        f"CostGuardianCallback-{environment}",
        environment=environment,
        table=storage_stack.table,
        env=aws_env,
    )

    # Callback depends on storage (for table)
    callback_stack.add_dependency(storage_stack)

    # Apply tags to callback stack
    for key, value in tags.items():
        cdk.Tags.of(callback_stack).add(key, value)

    # Create the events stack for Slack @mentions and DMs
    # Note: Events imports config secret by name, no cross-stack dependency
    events_stack = EventsStack(
        app,
        f"CostGuardianEvents-{environment}",
        environment=environment,
        table=storage_stack.table,
        config_bucket=storage_stack.config_bucket,
        env=aws_env,
    )

    # Events depends on storage (for table and bucket)
    events_stack.add_dependency(storage_stack)

    # Apply tags to events stack
    for key, value in tags.items():
        cdk.Tags.of(events_stack).add(key, value)

    app.synth()


if __name__ == "__main__":
    main()