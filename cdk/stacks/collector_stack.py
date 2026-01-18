"""CDK stack for Cost Collector Lambda and EventBridge schedule."""

from aws_cdk import (
    BundlingOptions,
    DockerImage,
    Duration,
    Stack,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class CollectorStack(Stack):
    """
    Cost Collector Lambda infrastructure.

    Creates:
    - Cost Collector Lambda function
    - EventBridge schedule for periodic execution
    - IAM role with permissions for Cost Explorer, DynamoDB, S3, Secrets Manager
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        environment: str,
        table: dynamodb.ITable,
        config_bucket: s3.IBucket,
        schedule_hours: list[int] | None = None,
        **kwargs,
    ) -> None:
        """
        Initialize the Collector stack.

        Args:
            scope: CDK scope.
            construct_id: Stack ID.
            environment: Deployment environment (dev, staging, prod).
            table: DynamoDB table for cost data.
            config_bucket: S3 bucket for configuration.
            schedule_hours: UTC hours to run collection (default: [6, 12, 18, 0]).
        """
        super().__init__(scope, construct_id, **kwargs)

        self.deploy_env = environment
        self.schedule_hours = schedule_hours or [6, 12, 18, 0]

        # Create the Slack webhook secret (user must populate after deployment)
        self.slack_secret = self._create_slack_secret()

        # Create the Lambda function
        self.collector_function = self._create_collector_lambda(
            table=table,
            config_bucket=config_bucket,
        )

        # Grant permissions
        self._grant_permissions(
            table=table,
            config_bucket=config_bucket,
        )

        # Create EventBridge schedule
        self._create_schedule()

    def _create_slack_secret(self) -> secretsmanager.Secret:
        """Create the Secrets Manager secret for Slack webhooks."""
        return secretsmanager.Secret(
            self,
            "SlackSecret",
            secret_name=f"cost-guardian/{self.deploy_env}/slack",
            description="Slack webhook URLs for Cost Guardian notifications",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"webhook_url_critical":"","webhook_url_heartbeat":""}',
                generate_string_key="placeholder",  # Not used, just required
            ),
        )

    def _create_collector_lambda(
        self,
        table: dynamodb.ITable,
        config_bucket: s3.IBucket,
    ) -> lambda_.Function:
        """Create the Cost Collector Lambda function."""
        return lambda_.Function(
            self,
            "CostCollectorFunction",
            function_name=f"cost-guardian-collector-{self.deploy_env}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="slack_aws_cost_guardian.handlers.cost_collector.handler",
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
            timeout=Duration.minutes(5),
            memory_size=512,
            environment={
                "TABLE_NAME": table.table_name,
                "CONFIG_BUCKET": config_bucket.bucket_name,
                "SLACK_SECRET_NAME": self.slack_secret.secret_name,
                "CONFIG_ENV": self.deploy_env,
            },
            description="Collects AWS cost data, detects anomalies, and sends notifications",
        )

    def _grant_permissions(
        self,
        table: dynamodb.ITable,
        config_bucket: s3.IBucket,
    ) -> None:
        """Grant necessary permissions to the Lambda function."""
        # DynamoDB permissions
        table.grant_read_write_data(self.collector_function)

        # S3 permissions (read config)
        config_bucket.grant_read(self.collector_function)

        # Secrets Manager permissions
        self.slack_secret.grant_read(self.collector_function)

        # Cost Explorer permissions
        self.collector_function.add_to_role_policy(
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

        # Budgets permissions
        self.collector_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="BudgetsAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "budgets:ViewBudget",
                    "budgets:DescribeBudgets",
                    "budgets:DescribeBudget",
                ],
                resources=["*"],
            )
        )

        # STS permissions (for getting account ID)
        self.collector_function.add_to_role_policy(
            iam.PolicyStatement(
                sid="STSAccess",
                effect=iam.Effect.ALLOW,
                actions=["sts:GetCallerIdentity"],
                resources=["*"],
            )
        )

    def _create_schedule(self) -> None:
        """Create EventBridge schedule for periodic cost collection."""
        # Create a rule for each scheduled hour
        for hour in self.schedule_hours:
            rule = events.Rule(
                self,
                f"CollectorSchedule{hour:02d}",
                rule_name=f"cost-guardian-schedule-{hour:02d}-{self.deploy_env}",
                description=f"Trigger cost collection at {hour:02d}:00 UTC",
                schedule=events.Schedule.cron(
                    minute="0",
                    hour=str(hour),
                ),
            )
            rule.add_target(targets.LambdaFunction(self.collector_function))

    @property
    def function_arn(self) -> str:
        """Get the Lambda function ARN."""
        return self.collector_function.function_arn

    @property
    def slack_secret_arn(self) -> str:
        """Get the Slack secret ARN."""
        return self.slack_secret.secret_arn