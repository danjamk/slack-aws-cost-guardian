"""Pydantic configuration schema for Slack AWS Cost Guardian."""

from typing import Literal

from pydantic import BaseModel, Field


class AWSConfig(BaseModel):
    """AWS account configuration."""

    region: str = "us-east-1"
    account_id: str | None = None  # Auto-detected if not provided


class CostExplorerSourceConfig(BaseModel):
    """Cost Explorer data source configuration."""

    enabled: bool = True
    granularity: Literal["DAILY", "HOURLY"] = "DAILY"
    lookback_days: int = Field(default=14, ge=1, le=90)


class BudgetsSourceConfig(BaseModel):
    """AWS Budgets data source configuration."""

    enabled: bool = True


class CollectionSourcesConfig(BaseModel):
    """Cost collection data sources."""

    cost_explorer: CostExplorerSourceConfig = Field(default_factory=CostExplorerSourceConfig)
    budgets: BudgetsSourceConfig = Field(default_factory=BudgetsSourceConfig)


class RetentionConfig(BaseModel):
    """Data retention configuration (in days)."""

    hourly_days: int = Field(default=7, ge=1)
    daily_days: int = Field(default=90, ge=1)
    monthly_days: int = Field(default=730, ge=1)  # 2 years


class ScheduleConfig(BaseModel):
    """Collection schedule configuration."""

    frequency: Literal["hourly", "4x_daily", "2x_daily", "daily"] = "4x_daily"
    hours: list[int] = Field(default=[6, 12, 18, 0])  # UTC hours for 4x_daily
    timezone: str = "UTC"


class CollectionConfig(BaseModel):
    """Cost collection configuration."""

    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    sources: CollectionSourcesConfig = Field(default_factory=CollectionSourcesConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)


class MonthlyBudgetConfig(BaseModel):
    """Monthly budget configuration."""

    amount: float = Field(default=900.0, ge=0)
    currency: str = "USD"
    warning_threshold: int = Field(default=80, ge=0, le=100)  # Percentage
    critical_threshold: int = Field(default=100, ge=0, le=200)  # Percentage


class DailyBudgetConfig(BaseModel):
    """Daily budget configuration."""

    amount: float = Field(default=30.0, ge=0)
    warning_threshold: int = Field(default=100, ge=0, le=200)  # Percentage


class BudgetConfig(BaseModel):
    """Budget configuration."""

    monthly: MonthlyBudgetConfig = Field(default_factory=MonthlyBudgetConfig)
    daily: DailyBudgetConfig = Field(default_factory=DailyBudgetConfig)


class AnomalyThresholdsConfig(BaseModel):
    """Anomaly detection thresholds."""

    absolute: float = Field(default=100.0, ge=0)  # Dollar amount
    percent_change: float = Field(default=50.0, ge=0)  # Percentage
    std_deviations: float = Field(default=2.5, ge=0)  # Standard deviations


class AnomalyFiltersConfig(BaseModel):
    """Anomaly detection filters."""

    minimum_cost: float = Field(default=5.0, ge=0)  # Ignore anomalies under this
    new_service_minimum: float = Field(default=1.0, ge=0)  # New service threshold


class AnomalyDetectionConfig(BaseModel):
    """Anomaly detection configuration."""

    enabled: bool = True
    baseline_days: int = Field(default=14, ge=1, le=90)
    thresholds: AnomalyThresholdsConfig = Field(default_factory=AnomalyThresholdsConfig)
    filters: AnomalyFiltersConfig = Field(default_factory=AnomalyFiltersConfig)
    alert_on_new_services: bool = True


class AnthropicConfig(BaseModel):
    """Anthropic API configuration."""

    model_id: str = "claude-sonnet-4-20250514"
    # api_key loaded from Secrets Manager


class OpenAIConfig(BaseModel):
    """OpenAI API configuration."""

    model_id: str = "gpt-4o"
    # api_key loaded from Secrets Manager


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    temperature: float = Field(default=0.3, ge=0, le=1)
    max_tokens: int = Field(default=2000, ge=100, le=8000)


class SlackChannelConfig(BaseModel):
    """Slack channel configuration."""

    name: str
    webhook_secret_key: str  # Key name in Secrets Manager


class SlackFeaturesConfig(BaseModel):
    """Slack features configuration."""

    interactive_buttons: bool = True
    thread_replies: bool = False  # Phase 2


class SlackConfig(BaseModel):
    """Slack integration configuration."""

    enabled: bool = True
    channels: dict[str, SlackChannelConfig] = Field(
        default_factory=lambda: {
            "critical": SlackChannelConfig(
                name="#aws-alerts-critical",
                webhook_secret_key="webhook_url_critical",
            ),
            "heartbeat": SlackChannelConfig(
                name="#aws-alerts-general",
                webhook_secret_key="webhook_url_heartbeat",
            ),
        }
    )
    features: SlackFeaturesConfig = Field(default_factory=SlackFeaturesConfig)


class DailyReportConfig(BaseModel):
    """Daily report configuration."""

    enabled: bool = True
    schedule_hour: int = Field(default=8, ge=0, le=23)  # UTC
    channel: str = "heartbeat"
    include_ai_insights: bool = True


class WeeklyReportConfig(BaseModel):
    """Weekly report configuration."""

    enabled: bool = True
    schedule_day: Literal[
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
    ] = "monday"
    schedule_hour: int = Field(default=8, ge=0, le=23)  # UTC
    channel: str = "heartbeat"
    include_ai_insights: bool = True


class ReportConfig(BaseModel):
    """Reporting configuration."""

    daily: DailyReportConfig = Field(default_factory=DailyReportConfig)
    weekly: WeeklyReportConfig = Field(default_factory=WeeklyReportConfig)


class RoutingConfig(BaseModel):
    """Notification routing configuration."""

    budget_warning: str = "heartbeat"
    budget_critical: str = "critical"
    anomaly_warning: str = "heartbeat"
    anomaly_critical: str = "critical"
    daily_report: str = "heartbeat"
    weekly_report: str = "heartbeat"


class GuardianContextConfig(BaseModel):
    """Guardian context file configuration."""

    s3_key: str = "config/guardian-context.md"
    local_path: str = "config/guardian-context.md"


class Config(BaseModel):
    """Root configuration for Slack AWS Cost Guardian."""

    project_name: str = "slack-aws-cost-guardian"
    environment: Literal["dev", "staging", "prod"] = "dev"

    aws: AWSConfig = Field(default_factory=AWSConfig)
    collection: CollectionConfig = Field(default_factory=CollectionConfig)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    anomaly_detection: AnomalyDetectionConfig = Field(default_factory=AnomalyDetectionConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    reports: ReportConfig = Field(default_factory=ReportConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    guardian_context: GuardianContextConfig = Field(default_factory=GuardianContextConfig)

    # Resource tags
    tags: dict[str, str] = Field(
        default_factory=lambda: {
            "Project": "slack-aws-cost-guardian",
            "ManagedBy": "CDK",
        }
    )