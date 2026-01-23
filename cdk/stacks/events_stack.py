"""CDK stack for Slack Events handler Lambda."""

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    DockerImage,
    Duration,
    Stack,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class EventsStack(Stack):
    """
    Slack Events handler infrastructure.

    Creates:
    - Lambda function to handle Slack @mentions and DMs
    - Function URL for Slack Events API
    - IAM permissions for Cost Explorer, DynamoDB, S3, Secrets Manager
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        environment: str,
        table: dynamodb.ITable,
        config_bucket: s3.IBucket,
        **kwargs,
    ) -> None:
        """
        Initialize the Events stack.

        Args:
            scope: CDK scope.
            construct_id: Stack ID.
            environment: Deployment environment (dev, staging, prod).
            table: DynamoDB table for cost data.
            config_bucket: S3 bucket for configuration.
        """
        super().__init__(scope, construct_id, **kwargs)

        self.deploy_env = environment

        # Import config secret by name (avoids cross-stack reference issues)
        config_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "ConfigSecret",
            secret_name=f"cost-guardian/{environment}/config",
        )

        # Create the Events Lambda
        self.events_function = self._create_events_lambda(
            table=table,
            config_bucket=config_bucket,
            config_secret=config_secret,
        )

        # Grant permissions
        self._grant_permissions(
            table=table,
            config_bucket=config_bucket,
            config_secret=config_secret,
        )

        # Create Function URL (public - security via signature verification)
        self.function_url = self.events_function.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
        )

        # Output the URL for Slack configuration
        CfnOutput(
            self,
            "EventsUrl",
            value=self.function_url.url,
            description="Configure this URL in Slack App Event Subscriptions",
        )

    def _create_events_lambda(
        self,
        table: dynamodb.ITable,
        config_bucket: s3.IBucket,
        config_secret: secretsmanager.ISecret,
    ) -> lambda_.Function:
        """Create the Slack Events Lambda function."""
        return lambda_.Function(
            self,
            "SlackEventsFunction",
            function_name=f"cost-guardian-events-{self.deploy_env}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="slack_aws_cost_guardian.handlers.slack_events.handler",
            code=lambda_.Code.from_asset(
                ".",
                bundling=BundlingOptions(
                    image=DockerImage.from_registry(
                        "public.ecr.aws/sam/build-python3.12:latest"
                    ),
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements-lambda.txt -t /asset-output && "
                        "cp -r src/slack_aws_cost_guardian /asset-output/ && "
                        "cp -r config /asset-output/",
                    ],
                ),
                exclude=[
                    "cdk.out",
                    ".git",
                    ".venv",
                    "*.pyc",
                    "__pycache__",
                    ".pytest_cache",
                    "tests",
                    "*.md",
                    "Makefile",
                    ".env*",
                    "reference",
                ],
            ),
            timeout=Duration.seconds(30),  # Slack expects fast acknowledgment
            memory_size=512,  # For LLM tool-use loops
            environment={
                "TABLE_NAME": table.table_name,
                "CONFIG_BUCKET": config_bucket.bucket_name,
                "CONFIG_SECRET_NAME": config_secret.secret_name,
                "CONFIG_ENV": self.deploy_env,
            },
            description="Handles Slack @mentions and DMs for cost queries",
        )

    def _grant_permissions(
        self,
        table: dynamodb.ITable,
        config_bucket: s3.IBucket,
        config_secret: secretsmanager.ISecret,
    ) -> None:
        """Grant necessary permissions to the Lambda function."""
        # DynamoDB permissions (read for cost data, write for deduplication)
        table.grant_read_write_data(self.events_function)

        # S3 permissions (read config)
        config_bucket.grant_read(self.events_function)

        # Secrets Manager permissions
        config_secret.grant_read(self.events_function)

        # Cost Explorer permissions (for real-time queries)
        self.events_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="CostExplorerAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "ce:GetCostAndUsage",
                    "ce:GetCostForecast",
                ],
                resources=["*"],
            )
        )

        # STS permissions (for getting account ID)
        self.events_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="STSAccess",
                effect=iam.Effect.ALLOW,
                actions=["sts:GetCallerIdentity"],
                resources=["*"],
            )
        )

    @property
    def events_url(self) -> str:
        """Get the Events Function URL."""
        return self.function_url.url