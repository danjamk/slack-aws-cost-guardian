#!/usr/bin/env python3
"""CDK application entry point for Slack AWS Cost Guardian."""

import os

import aws_cdk as cdk

from cdk.stacks.storage_stack import StorageStack
from cdk.stacks.collector_stack import CollectorStack


def main():
    """Create and synthesize the CDK application."""
    app = cdk.App()

    # Get environment from context or environment variable
    environment = app.node.try_get_context("environment") or os.environ.get(
        "CONFIG_ENV", "dev"
    )

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
    collector_stack = CollectorStack(
        app,
        f"CostGuardianCollector-{environment}",
        environment=environment,
        table=storage_stack.table,
        config_bucket=storage_stack.config_bucket,
        schedule_hours=[6, 12, 18, 0],  # 4x daily
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
        "SlackSecretArn",
        value=collector_stack.slack_secret_arn,
        description="Secrets Manager ARN for Slack webhooks (populate after deployment)",
    )

    app.synth()


if __name__ == "__main__":
    main()