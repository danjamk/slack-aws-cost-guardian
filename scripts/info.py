#!/usr/bin/env python3
"""Display Cost Guardian deployment information."""

import argparse
import json
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import yaml
from botocore.exceptions import ClientError


# ANSI colors (match Makefile)
BLUE = "\033[34m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM = "\033[2m"


@dataclass
class EnvironmentInfo:
    account_id: str
    region: str
    environment: str
    caller_arn: str


@dataclass
class LambdaInfo:
    function_name: str
    runtime: str
    memory_mb: int
    timeout_seconds: int
    last_modified: str
    state: str
    architecture: str
    function_url: str | None = None


@dataclass
class SlackInfo:
    critical_channel: str
    heartbeat_channel: str
    webhook_critical_masked: str
    webhook_heartbeat_masked: str
    callback_url: str | None
    signing_secret_configured: bool


@dataclass
class SecretsInfo:
    slack_secret_name: str
    slack_configured: bool
    llm_secret_name: str
    anthropic_configured: bool
    openai_configured: bool


@dataclass
class LLMInfo:
    provider: str
    model_id: str
    configured: bool


@dataclass
class ScheduleInfo:
    rule_name: str
    description: str
    schedule_expression: str
    state: str
    next_invocation: str | None = None


@dataclass
class DynamoDBInfo:
    table_name: str
    status: str
    item_count: int
    size_bytes: int
    snapshot_count: int = 0
    feedback_count: int = 0
    change_count: int = 0


@dataclass
class BudgetConfigInfo:
    monthly_amount: float
    currency: str
    warning_threshold: int
    critical_threshold: int


@dataclass
class RecentCostInfo:
    last_snapshot_date: str | None
    last_snapshot_hour: int | None
    total_cost: float | None
    top_services: dict[str, float] = field(default_factory=dict)


@dataclass
class GuardianContextInfo:
    s3_location: str | None
    exists: bool
    last_modified: str | None
    content: str | None


class CostGuardianInfo:
    """Collect and display Cost Guardian deployment info."""

    def __init__(self, environment: str, show_secrets: bool = False):
        self.env = environment
        self.show_secrets = show_secrets
        self._cf_outputs: dict[str, dict[str, str]] = {}

        # Initialize boto3 clients
        self.cf_client = boto3.client("cloudformation")
        self.lambda_client = boto3.client("lambda")
        self.events_client = boto3.client("events")
        self.dynamodb_client = boto3.client("dynamodb")
        self.dynamodb = boto3.resource("dynamodb")
        self.s3_client = boto3.client("s3")
        self.secrets_client = boto3.client("secretsmanager")
        self.sts_client = boto3.client("sts")

    def _get_cf_outputs(self, stack_name: str) -> dict[str, str]:
        """Get all CloudFormation outputs for a stack (cached)."""
        if stack_name not in self._cf_outputs:
            try:
                response = self.cf_client.describe_stacks(StackName=stack_name)
                outputs = response["Stacks"][0].get("Outputs", [])
                self._cf_outputs[stack_name] = {
                    o["OutputKey"]: o["OutputValue"] for o in outputs
                }
            except ClientError:
                self._cf_outputs[stack_name] = {}
        return self._cf_outputs[stack_name]

    def _get_cf_output(self, stack_name: str, output_key: str) -> str | None:
        """Get a single CloudFormation output."""
        return self._get_cf_outputs(stack_name).get(output_key)

    def _mask_webhook(self, url: str | None) -> str:
        """Mask a webhook URL."""
        if not url:
            return "Not configured"
        if self.show_secrets:
            return url
        if "hooks.slack.com" in url:
            # Show last 8 characters
            return f"https://hooks.slack.com/.../{url[-8:]}"
        return url[:30] + "..."

    def _mask_api_key(self, key: str | None, prefix: str = "") -> str:
        """Mask an API key."""
        if not key:
            return "Not configured"
        if self.show_secrets:
            return key
        if len(key) > 8:
            return f"{prefix}***...{key[-4:]}"
        return "***configured***"

    def get_environment_info(self) -> EnvironmentInfo | None:
        """Get AWS environment information."""
        try:
            identity = self.sts_client.get_caller_identity()
            session = boto3.session.Session()
            return EnvironmentInfo(
                account_id=identity["Account"],
                region=session.region_name or "us-east-1",
                environment=self.env,
                caller_arn=identity["Arn"],
            )
        except ClientError as e:
            print(f"{YELLOW}Warning: Could not get environment info: {e}{RESET}")
            return None

    def get_lambda_info(self) -> list[LambdaInfo]:
        """Get Lambda function information."""
        lambdas = []
        function_names = [
            f"cost-guardian-collector-{self.env}",
            f"cost-guardian-slack-callback-{self.env}",
        ]

        for func_name in function_names:
            try:
                response = self.lambda_client.get_function(FunctionName=func_name)
                config = response["Configuration"]

                # Get function URL if it exists
                function_url = None
                try:
                    url_response = self.lambda_client.get_function_url_config(
                        FunctionName=func_name
                    )
                    function_url = url_response.get("FunctionUrl")
                except ClientError:
                    pass

                lambdas.append(
                    LambdaInfo(
                        function_name=func_name,
                        runtime=config["Runtime"],
                        memory_mb=config["MemorySize"],
                        timeout_seconds=config["Timeout"],
                        last_modified=config["LastModified"],
                        state=config["State"],
                        architecture=config.get("Architectures", ["x86_64"])[0],
                        function_url=function_url,
                    )
                )
            except ClientError as e:
                if e.response["Error"]["Code"] == "ResourceNotFoundException":
                    continue
                print(f"{YELLOW}Warning: Could not get Lambda info for {func_name}: {e}{RESET}")

        return lambdas

    def get_slack_info(self) -> SlackInfo | None:
        """Get Slack configuration."""
        try:
            # Get config for channel names
            config = self._load_config()
            critical_channel = config.get("slack", {}).get("channels", {}).get("critical", {}).get("name", "#aws-alerts-critical")
            heartbeat_channel = config.get("slack", {}).get("channels", {}).get("heartbeat", {}).get("name", "#aws-alerts-general")

            # Get callback URL from CloudFormation
            callback_url = self._get_cf_output(
                f"CostGuardianCallback-{self.env}", "CallbackUrl"
            )

            # Get webhook secrets
            secret_arn = self._get_cf_output(
                f"CostGuardianCollector-{self.env}", "SlackSecretArn"
            )

            webhook_critical = None
            webhook_heartbeat = None
            signing_secret_configured = False

            if secret_arn:
                try:
                    secret_response = self.secrets_client.get_secret_value(
                        SecretId=secret_arn
                    )
                    secret_data = json.loads(secret_response["SecretString"])
                    webhook_critical = secret_data.get("webhook_url_critical")
                    webhook_heartbeat = secret_data.get("webhook_url_heartbeat")
                    signing_secret = secret_data.get("signing_secret")
                    signing_secret_configured = bool(signing_secret and signing_secret.strip())
                except ClientError:
                    pass

            return SlackInfo(
                critical_channel=critical_channel,
                heartbeat_channel=heartbeat_channel,
                webhook_critical_masked=self._mask_webhook(webhook_critical),
                webhook_heartbeat_masked=self._mask_webhook(webhook_heartbeat),
                callback_url=callback_url,
                signing_secret_configured=signing_secret_configured,
            )
        except Exception as e:
            print(f"{YELLOW}Warning: Could not get Slack info: {e}{RESET}")
            return None

    def get_secrets_info(self) -> SecretsInfo | None:
        """Get secrets configuration status."""
        try:
            slack_secret_arn = self._get_cf_output(
                f"CostGuardianCollector-{self.env}", "SlackSecretArn"
            )
            llm_secret_arn = self._get_cf_output(
                f"CostGuardianCollector-{self.env}", "LLMSecretArn"
            )

            slack_configured = False
            anthropic_configured = False
            openai_configured = False

            if slack_secret_arn:
                try:
                    secret_response = self.secrets_client.get_secret_value(
                        SecretId=slack_secret_arn
                    )
                    secret_data = json.loads(secret_response["SecretString"])
                    webhook = secret_data.get("webhook_url_critical", "")
                    slack_configured = bool(webhook and "YOUR" not in webhook)
                except ClientError:
                    pass

            if llm_secret_arn:
                try:
                    secret_response = self.secrets_client.get_secret_value(
                        SecretId=llm_secret_arn
                    )
                    secret_data = json.loads(secret_response["SecretString"])
                    anthropic_configured = bool(secret_data.get("anthropic_api_key"))
                    openai_configured = bool(secret_data.get("openai_api_key"))
                except ClientError:
                    pass

            # Extract secret names from ARNs
            slack_name = slack_secret_arn.split(":")[-1] if slack_secret_arn else "Not found"
            llm_name = llm_secret_arn.split(":")[-1] if llm_secret_arn else "Not found"

            return SecretsInfo(
                slack_secret_name=slack_name,
                slack_configured=slack_configured,
                llm_secret_name=llm_name,
                anthropic_configured=anthropic_configured,
                openai_configured=openai_configured,
            )
        except Exception as e:
            print(f"{YELLOW}Warning: Could not get secrets info: {e}{RESET}")
            return None

    def get_llm_info(self) -> LLMInfo | None:
        """Get LLM provider configuration."""
        try:
            config = self._load_config()
            llm_config = config.get("llm", {})
            provider = llm_config.get("provider", "anthropic")

            # Get model ID based on provider
            if provider == "anthropic":
                model_id = llm_config.get("anthropic", {}).get("model_id", "claude-sonnet-4-20250514")
            else:
                model_id = llm_config.get("openai", {}).get("model_id", "gpt-4o")

            # Check if configured
            secrets_info = self.get_secrets_info()
            configured = False
            if secrets_info:
                if provider == "anthropic":
                    configured = secrets_info.anthropic_configured
                else:
                    configured = secrets_info.openai_configured

            return LLMInfo(
                provider=provider,
                model_id=model_id,
                configured=configured,
            )
        except Exception as e:
            print(f"{YELLOW}Warning: Could not get LLM info: {e}{RESET}")
            return None

    def get_schedule_info(self) -> list[ScheduleInfo]:
        """Get EventBridge schedule information."""
        schedules = []
        try:
            response = self.events_client.list_rules(NamePrefix="cost-guardian")
            for rule in response.get("Rules", []):
                if self.env not in rule["Name"]:
                    continue

                schedule_expr = rule.get("ScheduleExpression", "")
                description = rule.get("Description", "")

                # Try to calculate next invocation
                next_invocation = self._calculate_next_run(schedule_expr)

                schedules.append(
                    ScheduleInfo(
                        rule_name=rule["Name"],
                        description=description,
                        schedule_expression=schedule_expr,
                        state=rule.get("State", "UNKNOWN"),
                        next_invocation=next_invocation,
                    )
                )
        except ClientError as e:
            print(f"{YELLOW}Warning: Could not get EventBridge schedules: {e}{RESET}")

        return sorted(schedules, key=lambda x: x.rule_name)

    def _calculate_next_run(self, schedule_expr: str) -> str | None:
        """Calculate next run time from cron expression (simplified)."""
        # This is a simplified implementation - just returns the schedule description
        if schedule_expr.startswith("cron("):
            return f"Schedule: {schedule_expr}"
        elif schedule_expr.startswith("rate("):
            return f"Interval: {schedule_expr}"
        return None

    def get_dynamodb_info(self) -> DynamoDBInfo | None:
        """Get DynamoDB table information."""
        try:
            table_name = self._get_cf_output(
                f"CostGuardianStorage-{self.env}", "TableName"
            )
            if not table_name:
                return None

            response = self.dynamodb_client.describe_table(TableName=table_name)
            table = response["Table"]

            # Count items by type
            snapshot_count = self._count_items_by_prefix(table_name, "SNAPSHOT#")
            feedback_count = self._count_items_by_prefix(table_name, "FEEDBACK#")
            change_count = self._count_items_by_prefix(table_name, "CHANGE#")

            return DynamoDBInfo(
                table_name=table_name,
                status=table["TableStatus"],
                item_count=table.get("ItemCount", 0),
                size_bytes=table.get("TableSizeBytes", 0),
                snapshot_count=snapshot_count,
                feedback_count=feedback_count,
                change_count=change_count,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            print(f"{YELLOW}Warning: Could not get DynamoDB info: {e}{RESET}")
            return None

    def _count_items_by_prefix(self, table_name: str, prefix: str) -> int:
        """Count items with a PK prefix."""
        try:
            response = self.dynamodb_client.scan(
                TableName=table_name,
                FilterExpression="begins_with(PK, :prefix)",
                ExpressionAttributeValues={":prefix": {"S": prefix}},
                Select="COUNT",
            )
            return response.get("Count", 0)
        except ClientError:
            return 0

    def get_budget_config(self) -> BudgetConfigInfo | None:
        """Get budget configuration from config file."""
        try:
            config = self._load_config()
            budgets = config.get("budgets", {}).get("monthly", {})

            return BudgetConfigInfo(
                monthly_amount=float(budgets.get("amount", 0)),
                currency=budgets.get("currency", "USD"),
                warning_threshold=int(budgets.get("warning_threshold", 80)),
                critical_threshold=int(budgets.get("critical_threshold", 100)),
            )
        except Exception as e:
            print(f"{YELLOW}Warning: Could not get budget config: {e}{RESET}")
            return None

    def get_recent_cost_info(self) -> RecentCostInfo | None:
        """Get recent cost data from DynamoDB."""
        try:
            table_name = self._get_cf_output(
                f"CostGuardianStorage-{self.env}", "TableName"
            )
            if not table_name:
                return None

            # Scan for recent snapshots
            response = self.dynamodb_client.scan(
                TableName=table_name,
                FilterExpression="begins_with(PK, :prefix)",
                ExpressionAttributeValues={":prefix": {"S": "SNAPSHOT#"}},
                Limit=10,
            )

            items = response.get("Items", [])
            if not items:
                return RecentCostInfo(
                    last_snapshot_date=None,
                    last_snapshot_hour=None,
                    total_cost=None,
                )

            # Find most recent
            latest = None
            for item in items:
                timestamp = item.get("timestamp", {}).get("S", "")
                if not latest or timestamp > latest.get("timestamp", {}).get("S", ""):
                    latest = item

            if not latest:
                return RecentCostInfo(
                    last_snapshot_date=None,
                    last_snapshot_hour=None,
                    total_cost=None,
                )

            # Extract cost by service
            cost_by_service = {}
            service_map = latest.get("cost_by_service", {}).get("M", {})
            for service, cost_data in service_map.items():
                cost_str = cost_data.get("N") or cost_data.get("S", "0")
                cost_by_service[service] = float(cost_str)

            # Sort by cost and take top 5
            top_services = dict(
                sorted(cost_by_service.items(), key=lambda x: x[1], reverse=True)[:5]
            )

            # Extract total_cost (can be stored as "S" or "N" type)
            total_cost_data = latest.get("total_cost", {})
            total_cost_str = total_cost_data.get("S") or total_cost_data.get("N") or "0"
            total_cost = float(total_cost_str)

            return RecentCostInfo(
                last_snapshot_date=latest.get("date", {}).get("S"),
                last_snapshot_hour=int(latest.get("hour", {}).get("N", 0)),
                total_cost=total_cost,
                top_services=top_services,
            )
        except Exception as e:
            print(f"{YELLOW}Warning: Could not get recent cost info: {e}{RESET}")
            return None

    def get_guardian_context(self) -> GuardianContextInfo | None:
        """Get guardian context from S3."""
        try:
            bucket_name = self._get_cf_output(
                f"CostGuardianStorage-{self.env}", "ConfigBucketName"
            )
            if not bucket_name:
                return None

            s3_key = "config/guardian-context.md"
            s3_location = f"s3://{bucket_name}/{s3_key}"

            try:
                response = self.s3_client.get_object(Bucket=bucket_name, Key=s3_key)
                content = response["Body"].read().decode("utf-8")
                last_modified = response["LastModified"].isoformat()

                return GuardianContextInfo(
                    s3_location=s3_location,
                    exists=True,
                    last_modified=last_modified,
                    content=content,
                )
            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey":
                    return GuardianContextInfo(
                        s3_location=s3_location,
                        exists=False,
                        last_modified=None,
                        content=None,
                    )
                raise
        except Exception as e:
            print(f"{YELLOW}Warning: Could not get guardian context: {e}{RESET}")
            return None

    def _load_config(self) -> dict[str, Any]:
        """Load configuration from config.yaml."""
        config_path = Path(__file__).parent.parent / "config" / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                return yaml.safe_load(f) or {}
        return {}

    def collect_all(self) -> dict[str, Any]:
        """Collect all info sections."""
        return {
            "environment": self.get_environment_info(),
            "lambdas": self.get_lambda_info(),
            "slack": self.get_slack_info(),
            "secrets": self.get_secrets_info(),
            "llm": self.get_llm_info(),
            "schedules": self.get_schedule_info(),
            "dynamodb": self.get_dynamodb_info(),
            "budget": self.get_budget_config(),
            "recent_cost": self.get_recent_cost_info(),
            "guardian_context": self.get_guardian_context(),
        }

    def print_formatted(self, data: dict[str, Any]) -> None:
        """Print colored, formatted output."""
        print(f"\n{BLUE}{BOLD}Cost Guardian Info ({self.env}){RESET}")
        print("=" * 40)

        # Environment
        env_info = data.get("environment")
        if env_info:
            print(f"\n{BOLD}ENVIRONMENT{RESET}")
            print(f"  Account:     {env_info.account_id}")
            print(f"  Region:      {env_info.region}")
            print(f"  Environment: {env_info.environment}")
            print(f"  Caller:      {DIM}{env_info.caller_arn}{RESET}")
        else:
            print(f"\n{BOLD}ENVIRONMENT{RESET}")
            print(f"  {YELLOW}Could not retrieve environment info{RESET}")

        # Lambda Functions
        lambdas = data.get("lambdas", [])
        print(f"\n{BOLD}LAMBDA FUNCTIONS{RESET}")
        if lambdas:
            for lm in lambdas:
                print(f"  {GREEN}{lm.function_name}{RESET}")
                print(f"    Runtime: {lm.runtime} ({lm.architecture}) | Memory: {lm.memory_mb} MB | Timeout: {lm.timeout_seconds}s")
                print(f"    State: {lm.state} | Last Update: {lm.last_modified}")
                if lm.function_url:
                    print(f"    Function URL: {lm.function_url}")
        else:
            print(f"  {YELLOW}No Lambda functions found{RESET}")

        # Slack Configuration
        slack_info = data.get("slack")
        print(f"\n{BOLD}SLACK CONFIGURATION{RESET}")
        if slack_info:
            print(f"  Critical Channel:  {slack_info.critical_channel}")
            print(f"    Webhook: {slack_info.webhook_critical_masked}")
            print(f"  Heartbeat Channel: {slack_info.heartbeat_channel}")
            print(f"    Webhook: {slack_info.webhook_heartbeat_masked}")
            if slack_info.callback_url:
                print(f"  Callback URL: {slack_info.callback_url}")
            signing_status = f"{GREEN}Configured{RESET}" if slack_info.signing_secret_configured else f"{YELLOW}Not configured{RESET}"
            print(f"  Signing Secret: {signing_status}")
        else:
            print(f"  {YELLOW}Could not retrieve Slack configuration{RESET}")

        # Secrets Status
        secrets_info = data.get("secrets")
        print(f"\n{BOLD}SECRETS MANAGER{RESET}")
        if secrets_info:
            slack_status = f"{GREEN}Configured{RESET}" if secrets_info.slack_configured else f"{YELLOW}Not configured{RESET}"
            print(f"  Slack Secret: {secrets_info.slack_secret_name}")
            print(f"    Status: {slack_status}")
            print(f"  LLM Secret: {secrets_info.llm_secret_name}")
            anthropic_status = f"{GREEN}Configured{RESET}" if secrets_info.anthropic_configured else f"{DIM}Not configured{RESET}"
            openai_status = f"{GREEN}Configured{RESET}" if secrets_info.openai_configured else f"{DIM}Not configured{RESET}"
            print(f"    Anthropic: {anthropic_status}")
            print(f"    OpenAI: {openai_status}")
        else:
            print(f"  {YELLOW}Could not retrieve secrets info{RESET}")

        # LLM Provider
        llm_info = data.get("llm")
        print(f"\n{BOLD}LLM PROVIDER{RESET}")
        if llm_info:
            status = f"{GREEN}Ready{RESET}" if llm_info.configured else f"{YELLOW}Not configured{RESET}"
            print(f"  Provider: {llm_info.provider}")
            print(f"  Model:    {llm_info.model_id}")
            print(f"  Status:   {status}")
        else:
            print(f"  {YELLOW}Could not retrieve LLM info{RESET}")

        # EventBridge Schedules
        schedules = data.get("schedules", [])
        print(f"\n{BOLD}EVENTBRIDGE SCHEDULES{RESET}")
        if schedules:
            for sched in schedules:
                state_color = GREEN if sched.state == "ENABLED" else YELLOW
                print(f"  {sched.rule_name}")
                print(f"    {sched.schedule_expression} | State: {state_color}{sched.state}{RESET}")
        else:
            print(f"  {YELLOW}No EventBridge schedules found{RESET}")

        # DynamoDB
        dynamo_info = data.get("dynamodb")
        print(f"\n{BOLD}DYNAMODB{RESET}")
        if dynamo_info:
            print(f"  Table:  {dynamo_info.table_name}")
            print(f"  Status: {dynamo_info.status}")
            print(f"  Items:  {dynamo_info.item_count} total | Size: {dynamo_info.size_bytes:,} bytes")
            print(f"  Record Types:")
            print(f"    Snapshots: {dynamo_info.snapshot_count}")
            print(f"    Feedback:  {dynamo_info.feedback_count}")
            print(f"    Changes:   {dynamo_info.change_count}")
        else:
            print(f"  {YELLOW}DynamoDB table not found{RESET}")

        # Budget Configuration
        budget_info = data.get("budget")
        print(f"\n{BOLD}BUDGET CONFIGURATION{RESET}")
        if budget_info:
            warning_amount = budget_info.monthly_amount * budget_info.warning_threshold / 100
            critical_amount = budget_info.monthly_amount * budget_info.critical_threshold / 100
            print(f"  Monthly Budget:     ${budget_info.monthly_amount:.2f} {budget_info.currency}")
            print(f"  Warning Threshold:  {budget_info.warning_threshold}% (${warning_amount:.2f})")
            print(f"  Critical Threshold: {budget_info.critical_threshold}% (${critical_amount:.2f})")
        else:
            print(f"  {YELLOW}Could not load budget configuration{RESET}")

        # Recent Cost Data
        cost_info = data.get("recent_cost")
        print(f"\n{BOLD}RECENT COST DATA{RESET}")
        if cost_info and cost_info.last_snapshot_date:
            print(f"  Last Snapshot: {cost_info.last_snapshot_date} hour {cost_info.last_snapshot_hour}")
            print(f"  Total Cost:    ${cost_info.total_cost:.2f} (MTD)")
            if cost_info.top_services:
                print(f"  Top Services:")
                for service, cost in cost_info.top_services.items():
                    print(f"    {service}: ${cost:.2f}")
        else:
            print(f"  {YELLOW}No cost data available{RESET}")

        # Guardian Context
        context_info = data.get("guardian_context")
        print(f"\n{BOLD}GUARDIAN CONTEXT{RESET}")
        if context_info:
            print(f"  Location: {context_info.s3_location}")
            if context_info.exists:
                print(f"  Last Modified: {context_info.last_modified}")
                print(f"\n{DIM}--- Content ---{RESET}")
                print(context_info.content)
            else:
                print(f"  {YELLOW}Context file not found (run: make update-context){RESET}")
        else:
            print(f"  {YELLOW}Could not retrieve guardian context{RESET}")

        print()

    def print_json(self, data: dict[str, Any]) -> None:
        """Print JSON output."""
        # Convert dataclasses to dicts
        output = {}
        for key, value in data.items():
            if value is None:
                output[key] = None
            elif isinstance(value, list):
                output[key] = [asdict(item) if hasattr(item, "__dataclass_fields__") else item for item in value]
            elif hasattr(value, "__dataclass_fields__"):
                output[key] = asdict(value)
            else:
                output[key] = value

        print(json.dumps(output, indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(description="Cost Guardian Info")
    parser.add_argument("--env", default="dev", help="Environment (default: dev)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--show-secrets",
        action="store_true",
        help="Show full secret values (use with caution)",
    )
    args = parser.parse_args()

    try:
        info = CostGuardianInfo(args.env, show_secrets=args.show_secrets)
        data = info.collect_all()

        if args.json:
            info.print_json(data)
        else:
            info.print_formatted(data)
    except Exception as e:
        print(f"{YELLOW}Error: {e}{RESET}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()