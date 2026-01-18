"""CDK stack for DynamoDB tables and S3 config bucket."""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
)
from constructs import Construct


class StorageStack(Stack):
    """
    Storage infrastructure for Slack AWS Cost Guardian.

    Creates:
    - DynamoDB table for cost snapshots, anomaly feedback, and change log
    - S3 bucket for configuration (guardian context file)
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        deploy_env: str = "dev",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.deploy_env = deploy_env

        # Create the main DynamoDB table (single-table design)
        self.table = self._create_main_table()

        # Create the config bucket for guardian context
        self.config_bucket = self._create_config_bucket()

    def _create_main_table(self) -> dynamodb.Table:
        """
        Create the main DynamoDB table using single-table design.

        Key Structure:
        - cost_snapshots: PK=SNAPSHOT#{date}, SK=HOUR#{hour}#{account_id}
        - anomaly_feedback: PK=FEEDBACK#{date}, SK=ALERT#{alert_id}
        - change_log: PK=CHANGE#{service}, SK=DATE#{date}#{change_id}

        GSIs:
        - date-index: For querying snapshots by date
        - status-index: For querying active changes
        """
        table = dynamodb.Table(
            self,
            "MainTable",
            table_name=f"cost-guardian-{self.deploy_env}",
            partition_key=dynamodb.Attribute(
                name="PK",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="SK",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=(
                RemovalPolicy.DESTROY if self.deploy_env == "dev" else RemovalPolicy.RETAIN
            ),
            point_in_time_recovery=self.deploy_env != "dev",
            time_to_live_attribute="ttl",
        )

        # GSI for querying snapshots by date
        table.add_global_secondary_index(
            index_name="date-index",
            partition_key=dynamodb.Attribute(
                name="date",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI for querying active changes
        table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        return table

    def _create_config_bucket(self) -> s3.Bucket:
        """
        Create S3 bucket for configuration files.

        Stores:
        - config/guardian-context.md: AI context for cost analysis
        """
        bucket = s3.Bucket(
            self,
            "ConfigBucket",
            bucket_name=f"cost-guardian-config-{self.deploy_env}-{Stack.of(self).account}",
            removal_policy=(
                RemovalPolicy.DESTROY if self.deploy_env == "dev" else RemovalPolicy.RETAIN
            ),
            auto_delete_objects=self.deploy_env == "dev",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="DeleteOldVersions",
                    noncurrent_version_expiration=Duration.days(30),
                    enabled=True,
                ),
            ],
        )

        return bucket

    @property
    def table_name(self) -> str:
        """Get the DynamoDB table name."""
        return self.table.table_name

    @property
    def table_arn(self) -> str:
        """Get the DynamoDB table ARN."""
        return self.table.table_arn

    @property
    def config_bucket_name(self) -> str:
        """Get the config bucket name."""
        return self.config_bucket.bucket_name

    @property
    def config_bucket_arn(self) -> str:
        """Get the config bucket ARN."""
        return self.config_bucket.bucket_arn