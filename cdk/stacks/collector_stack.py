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
    - EventBridge schedules for daily and weekly reports
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
        daily_report_hour_utc: int = 14,  # 6am PST = 14:00 UTC
        weekly_report_hour_utc: int = 14,  # 6am PST = 14:00 UTC
        anthropic_costs_enabled: bool = False,
        version: str = "0.0.0",
        git_commit: str = "unknown",
        deploy_timestamp: str = "",
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
            daily_report_hour_utc: UTC hour for daily report (default: 14 = 6am PST).
            weekly_report_hour_utc: UTC hour for weekly Monday report (default: 14 = 6am PST).
            anthropic_costs_enabled: Enable Anthropic API cost collection (default: False).
            version: Application version from VERSION file.
            git_commit: Git commit hash for traceability.
            deploy_timestamp: Timestamp of deployment.
        """
        super().__init__(scope, construct_id, **kwargs)

        self.deploy_env = environment
        self.schedule_hours = schedule_hours or [6, 12, 18, 0]
        self.daily_report_hour_utc = daily_report_hour_utc
        self.weekly_report_hour_utc = weekly_report_hour_utc
        self.anthropic_costs_enabled = anthropic_costs_enabled
        self.version = version
        self.git_commit = git_commit
        self.deploy_timestamp = deploy_timestamp

        # Create the unified config secret (user must populate after deployment)
        self.config_secret = self._create_config_secret()

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

        # Create EventBridge schedules
        self._create_schedule()
        self._create_report_schedules()

    def _create_config_secret(self) -> secretsmanager.Secret:
        """Create the unified Secrets Manager secret for all app configuration."""
        # Template contains all configuration keys
        template = (
            '{"webhook_url_critical":"","webhook_url_heartbeat":"",'
            '"signing_secret":"","bot_token":"",'
            '"anthropic_api_key":"","anthropic_admin_api_key":"","openai_api_key":""}'
        )
        return secretsmanager.Secret(
            self,
            "ConfigSecret",
            secret_name=f"cost-guardian/{self.deploy_env}/config",
            description="Configuration and API keys for Cost Guardian",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=template,
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
                "CONFIG_SECRET_NAME": self.config_secret.secret_name,
                "CONFIG_ENV": self.deploy_env,
                "ANTHROPIC_COSTS_ENABLED": str(self.anthropic_costs_enabled).lower(),
                "APP_VERSION": self.version,
                "GIT_COMMIT": self.git_commit,
                "DEPLOY_TIMESTAMP": self.deploy_timestamp,
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
        self.config_secret.grant_read(self.collector_function)

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

    def _create_report_schedules(self) -> None:
        """Create EventBridge schedules for daily and weekly reports."""
        # Daily report schedule (every day at configured hour)
        daily_rule = events.Rule(
            self,
            "DailyReportSchedule",
            rule_name=f"cost-guardian-daily-report-{self.deploy_env}",
            description=f"Trigger daily cost report at {self.daily_report_hour_utc:02d}:00 UTC",
            schedule=events.Schedule.cron(
                minute="0",
                hour=str(self.daily_report_hour_utc),
            ),
        )
        daily_rule.add_target(
            targets.LambdaFunction(
                self.collector_function,
                event=events.RuleTargetInput.from_object({"report_type": "daily"}),
            )
        )

        # Weekly report schedule (Monday at configured hour)
        weekly_rule = events.Rule(
            self,
            "WeeklyReportSchedule",
            rule_name=f"cost-guardian-weekly-report-{self.deploy_env}",
            description=f"Trigger weekly cost report on Monday at {self.weekly_report_hour_utc:02d}:00 UTC",
            schedule=events.Schedule.cron(
                minute="0",
                hour=str(self.weekly_report_hour_utc),
                week_day="MON",
            ),
        )
        weekly_rule.add_target(
            targets.LambdaFunction(
                self.collector_function,
                event=events.RuleTargetInput.from_object({"report_type": "weekly"}),
            )
        )

    @property
    def function_arn(self) -> str:
        """Get the Lambda function ARN."""
        return self.collector_function.function_arn

    @property
    def config_secret_arn(self) -> str:
        """Get the config secret ARN."""
        return self.config_secret.secret_arn