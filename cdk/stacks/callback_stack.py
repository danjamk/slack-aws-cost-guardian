"""CDK stack for Slack callback handler Lambda."""

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    DockerImage,
    Duration,
    Stack,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class CallbackStack(Stack):
    """
    Slack callback handler infrastructure.

    Creates:
    - Lambda function to handle Slack interactive button clicks
    - Function URL for Slack to call (public, secured via signature verification)
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        environment: str,
        table: dynamodb.ITable,
        **kwargs,
    ) -> None:
        """
        Initialize the Callback stack.

        Args:
            scope: CDK scope.
            construct_id: Stack ID.
            environment: Deployment environment (dev, staging, prod).
            table: DynamoDB table for storing feedback.
        """
        super().__init__(scope, construct_id, **kwargs)

        self.deploy_env = environment

        # Import config secret by name (avoids cross-stack reference issues)
        config_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "ConfigSecret",
            secret_name=f"cost-guardian/{environment}/config",
        )

        # Create the callback Lambda
        self.callback_function = self._create_callback_lambda(table, config_secret)

        # Grant permissions
        table.grant_read_write_data(self.callback_function)
        config_secret.grant_read(self.callback_function)

        # Create Function URL (public - security via signature verification)
        self.function_url = self.callback_function.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
        )

        # Output the URL for Slack configuration
        CfnOutput(
            self,
            "CallbackUrl",
            value=self.function_url.url,
            description="Configure this URL in Slack App Interactivity settings",
        )

    def _create_callback_lambda(
        self,
        table: dynamodb.ITable,
        config_secret: secretsmanager.ISecret,
    ) -> lambda_.Function:
        """Create the Slack callback Lambda function."""
        return lambda_.Function(
            self,
            "SlackCallbackFunction",
            function_name=f"cost-guardian-slack-callback-{self.deploy_env}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="slack_aws_cost_guardian.handlers.slack_callback.handler",
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
                        "cp -r src/slack_aws_cost_guardian /asset-output/",
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
                    "config",  # Callback doesn't need config
                ],
            ),
            timeout=Duration.seconds(10),  # Slack expects fast response
            memory_size=256,
            environment={
                "TABLE_NAME": table.table_name,
                "CONFIG_SECRET_NAME": config_secret.secret_name,
            },
            description="Handles Slack interactive button clicks for feedback",
        )

    @property
    def callback_url(self) -> str:
        """Get the callback Function URL."""
        return self.function_url.url