"""Data models for DynamoDB storage."""

from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _generate_uuid() -> str:
    return str(uuid4())


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


class AnomalyInfo(BaseModel):
    """Information about a detected anomaly."""

    service: str
    amount: float  # Dollar amount of the anomaly
    percent_change: float  # Percentage change from baseline
    severity: Literal["info", "warning", "critical"]
    baseline_cost: float | None = None
    description: str | None = None


class BudgetStatus(BaseModel):
    """Budget utilization status."""

    monthly_budget: float
    monthly_spent: float
    monthly_percent: float
    daily_budget: float | None = None
    daily_spent: float | None = None
    daily_percent: float | None = None


class CostForecast(BaseModel):
    """Cost forecast information."""

    end_of_month: float
    confidence: Literal["low", "medium", "high"] = "medium"


class AccountCost(BaseModel):
    """Cost information for a linked account."""

    name: str
    cost: float


class CostSnapshot(BaseModel):
    """
    Periodic cost data snapshot.

    DynamoDB Key Structure:
    - PK: SNAPSHOT#{date} (e.g., "SNAPSHOT#2024-01-15")
    - SK: HOUR#{hour}#{account_id} (e.g., "HOUR#14#123456789012")

    Multi-provider support:
    - provider field identifies the cost source (aws, anthropic, etc.)
    - Defaults to "aws" for backward compatibility with existing data
    """

    snapshot_id: str = Field(default_factory=_generate_uuid)
    timestamp: str = Field(default_factory=_utc_now_iso)
    account_id: str
    date: str  # YYYY-MM-DD - when the snapshot was taken
    hour: int = Field(ge=0, le=23)
    period_type: Literal["hourly", "daily", "weekly"] = "daily"

    # The date the cost_by_service data actually represents
    # (may differ from snapshot date due to cost_data_lag_days setting)
    cost_data_date: str | None = None  # YYYY-MM-DD

    # Provider identification for multi-service support
    # Defaults to "aws" for backward compatibility
    provider: Literal["aws", "anthropic", "openai", "databricks"] = "aws"

    total_cost: float
    currency: str = "USD"

    cost_by_service: dict[str, float] = Field(default_factory=dict)
    cost_by_account: dict[str, AccountCost] = Field(default_factory=dict)

    budget_status: BudgetStatus | None = None
    forecast: CostForecast | None = None
    anomalies_detected: list[AnomalyInfo] = Field(default_factory=list)

    ttl: int | None = None  # Unix timestamp for TTL

    @property
    def pk(self) -> str:
        """Generate partition key."""
        return f"SNAPSHOT#{self.date}"

    @property
    def sk(self) -> str:
        """Generate sort key."""
        return f"HOUR#{self.hour:02d}#{self.account_id}"

    def to_dynamodb_item(self) -> dict:
        """Convert to DynamoDB item format."""
        item = {
            "PK": self.pk,
            "SK": self.sk,
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "account_id": self.account_id,
            "date": self.date,
            "hour": self.hour,
            "period_type": self.period_type,
            "provider": self.provider,  # Multi-provider support
            "total_cost": str(self.total_cost),  # DynamoDB Number from string
            "currency": self.currency,
            "cost_by_service": {k: str(v) for k, v in self.cost_by_service.items()},
        }

        if self.cost_data_date:
            item["cost_data_date"] = self.cost_data_date

        if self.cost_by_account:
            item["cost_by_account"] = {
                k: {"name": v.name, "cost": str(v.cost)}
                for k, v in self.cost_by_account.items()
            }

        if self.budget_status:
            item["budget_status"] = {
                "monthly_budget": str(self.budget_status.monthly_budget),
                "monthly_spent": str(self.budget_status.monthly_spent),
                "monthly_percent": str(self.budget_status.monthly_percent),
            }
            if self.budget_status.daily_budget is not None:
                item["budget_status"]["daily_budget"] = str(self.budget_status.daily_budget)
                item["budget_status"]["daily_spent"] = str(self.budget_status.daily_spent)
                item["budget_status"]["daily_percent"] = str(self.budget_status.daily_percent)

        if self.forecast:
            item["forecast"] = {
                "end_of_month": str(self.forecast.end_of_month),
                "confidence": self.forecast.confidence,
            }

        if self.anomalies_detected:
            item["anomalies_detected"] = [
                {
                    "service": a.service,
                    "amount": str(a.amount),
                    "percent_change": str(a.percent_change),
                    "severity": a.severity,
                }
                for a in self.anomalies_detected
            ]

        if self.ttl:
            item["ttl"] = self.ttl

        return item

    @classmethod
    def from_dynamodb_item(cls, item: dict) -> "CostSnapshot":
        """Create from DynamoDB item."""
        cost_by_service = {k: float(v) for k, v in item.get("cost_by_service", {}).items()}

        cost_by_account = {}
        if "cost_by_account" in item:
            cost_by_account = {
                k: AccountCost(name=v["name"], cost=float(v["cost"]))
                for k, v in item["cost_by_account"].items()
            }

        budget_status = None
        if "budget_status" in item:
            bs = item["budget_status"]
            budget_status = BudgetStatus(
                monthly_budget=float(bs["monthly_budget"]),
                monthly_spent=float(bs["monthly_spent"]),
                monthly_percent=float(bs["monthly_percent"]),
                daily_budget=float(bs["daily_budget"]) if "daily_budget" in bs else None,
                daily_spent=float(bs["daily_spent"]) if "daily_spent" in bs else None,
                daily_percent=float(bs["daily_percent"]) if "daily_percent" in bs else None,
            )

        forecast = None
        if "forecast" in item:
            forecast = CostForecast(
                end_of_month=float(item["forecast"]["end_of_month"]),
                confidence=item["forecast"].get("confidence", "medium"),
            )

        anomalies_detected = []
        if "anomalies_detected" in item:
            anomalies_detected = [
                AnomalyInfo(
                    service=a["service"],
                    amount=float(a["amount"]),
                    percent_change=float(a["percent_change"]),
                    severity=a["severity"],
                )
                for a in item["anomalies_detected"]
            ]

        return cls(
            snapshot_id=item["snapshot_id"],
            timestamp=item["timestamp"],
            account_id=item["account_id"],
            date=item["date"],
            hour=int(item["hour"]),
            period_type=item.get("period_type", "daily"),
            provider=item.get("provider", "aws"),  # Default to aws for backward compat
            cost_data_date=item.get("cost_data_date"),  # May be None for old snapshots
            total_cost=float(item["total_cost"]),
            currency=item.get("currency", "USD"),
            cost_by_service=cost_by_service,
            cost_by_account=cost_by_account,
            budget_status=budget_status,
            forecast=forecast,
            anomalies_detected=anomalies_detected,
            ttl=int(item["ttl"]) if "ttl" in item else None,
        )


class FeedbackType(str, Enum):
    """User feedback types for anomalies."""

    EXPECTED = "expected"
    UNEXPECTED = "unexpected"
    INVESTIGATING = "investigating"


class DurationType(str, Enum):
    """Duration type for cost changes."""

    ONE_TIME = "one_time"
    ONGOING = "ongoing"
    TEMPORARY = "temporary"
    UNKNOWN = "unknown"


class AnomalyFeedback(BaseModel):
    """
    User feedback for an anomaly alert.

    DynamoDB Key Structure:
    - PK: FEEDBACK#{date} (e.g., "FEEDBACK#2024-01-15")
    - SK: ALERT#{alert_id} (e.g., "ALERT#abc123")
    """

    feedback_id: str = Field(default_factory=_generate_uuid)
    alert_id: str
    timestamp: str = Field(default_factory=_utc_now_iso)
    date: str  # YYYY-MM-DD

    user_id: str  # Slack user ID
    user_name: str  # Slack display name
    feedback_type: FeedbackType

    affected_services: list[str] = Field(default_factory=list)
    cost_impact: float  # Dollar amount of anomaly

    explanation: str | None = None
    duration_type: DurationType = DurationType.UNKNOWN
    expected_duration_days: int | None = None
    related_link: str | None = None  # PR/ticket URL

    original_alert_summary: str | None = None
    ai_analysis_summary: str | None = None
    slack_thread_ts: str | None = None

    ttl: int | None = None  # Unix timestamp for TTL

    @property
    def pk(self) -> str:
        """Generate partition key."""
        return f"FEEDBACK#{self.date}"

    @property
    def sk(self) -> str:
        """Generate sort key."""
        return f"ALERT#{self.alert_id}"

    def to_dynamodb_item(self) -> dict:
        """Convert to DynamoDB item format."""
        item = {
            "PK": self.pk,
            "SK": self.sk,
            "feedback_id": self.feedback_id,
            "alert_id": self.alert_id,
            "timestamp": self.timestamp,
            "date": self.date,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "feedback_type": self.feedback_type.value,
            "affected_services": self.affected_services,
            "cost_impact": str(self.cost_impact),
            "duration_type": self.duration_type.value,
        }

        if self.explanation:
            item["explanation"] = self.explanation
        if self.expected_duration_days:
            item["expected_duration_days"] = self.expected_duration_days
        if self.related_link:
            item["related_link"] = self.related_link
        if self.original_alert_summary:
            item["original_alert_summary"] = self.original_alert_summary
        if self.ai_analysis_summary:
            item["ai_analysis_summary"] = self.ai_analysis_summary
        if self.slack_thread_ts:
            item["slack_thread_ts"] = self.slack_thread_ts
        if self.ttl:
            item["ttl"] = self.ttl

        return item

    @classmethod
    def from_dynamodb_item(cls, item: dict) -> "AnomalyFeedback":
        """Create from DynamoDB item."""
        return cls(
            feedback_id=item["feedback_id"],
            alert_id=item["alert_id"],
            timestamp=item["timestamp"],
            date=item["date"],
            user_id=item["user_id"],
            user_name=item["user_name"],
            feedback_type=FeedbackType(item["feedback_type"]),
            affected_services=item.get("affected_services", []),
            cost_impact=float(item["cost_impact"]),
            explanation=item.get("explanation"),
            duration_type=DurationType(item.get("duration_type", "unknown")),
            expected_duration_days=item.get("expected_duration_days"),
            related_link=item.get("related_link"),
            original_alert_summary=item.get("original_alert_summary"),
            ai_analysis_summary=item.get("ai_analysis_summary"),
            slack_thread_ts=item.get("slack_thread_ts"),
            ttl=int(item["ttl"]) if "ttl" in item else None,
        )


class ChangeStatus(str, Enum):
    """Status of a tracked cost change."""

    ACTIVE = "active"
    RESOLVED = "resolved"
    EXPIRED = "expired"


class ChangeType(str, Enum):
    """Type of cost change."""

    NEW_SERVICE = "new_service"
    COST_INCREASE = "cost_increase"
    COST_DECREASE = "cost_decrease"
    USAGE_PATTERN = "usage_pattern"


class ChangeLog(BaseModel):
    """
    Tracked cost change for AI context.

    DynamoDB Key Structure:
    - PK: CHANGE#{service} (e.g., "CHANGE#AmazonEC2")
    - SK: DATE#{date}#{change_id} (e.g., "DATE#2024-01-15#xyz789")
    """

    change_id: str = Field(default_factory=_generate_uuid)
    service: str  # AWS service name
    timestamp: str = Field(default_factory=_utc_now_iso)
    date: str  # YYYY-MM-DD

    change_type: ChangeType
    status: ChangeStatus = ChangeStatus.ACTIVE
    description: str

    baseline_cost: float
    new_cost: float
    percent_change: float

    acknowledged_by: str  # user_id
    acknowledged_at: str

    expected_end_date: str | None = None  # YYYY-MM-DD
    resolution_notes: str | None = None
    related_feedback_ids: list[str] = Field(default_factory=list)

    ttl: int | None = None  # Unix timestamp for TTL

    @property
    def pk(self) -> str:
        """Generate partition key."""
        return f"CHANGE#{self.service}"

    @property
    def sk(self) -> str:
        """Generate sort key."""
        return f"DATE#{self.date}#{self.change_id}"

    def to_dynamodb_item(self) -> dict:
        """Convert to DynamoDB item format."""
        item = {
            "PK": self.pk,
            "SK": self.sk,
            "change_id": self.change_id,
            "service": self.service,
            "timestamp": self.timestamp,
            "date": self.date,
            "change_type": self.change_type.value,
            "status": self.status.value,
            "description": self.description,
            "baseline_cost": str(self.baseline_cost),
            "new_cost": str(self.new_cost),
            "percent_change": str(self.percent_change),
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at,
        }

        if self.expected_end_date:
            item["expected_end_date"] = self.expected_end_date
        if self.resolution_notes:
            item["resolution_notes"] = self.resolution_notes
        if self.related_feedback_ids:
            item["related_feedback_ids"] = self.related_feedback_ids
        if self.ttl:
            item["ttl"] = self.ttl

        return item

    @classmethod
    def from_dynamodb_item(cls, item: dict) -> "ChangeLog":
        """Create from DynamoDB item."""
        return cls(
            change_id=item["change_id"],
            service=item["service"],
            timestamp=item["timestamp"],
            date=item["date"],
            change_type=ChangeType(item["change_type"]),
            status=ChangeStatus(item["status"]),
            description=item["description"],
            baseline_cost=float(item["baseline_cost"]),
            new_cost=float(item["new_cost"]),
            percent_change=float(item["percent_change"]),
            acknowledged_by=item["acknowledged_by"],
            acknowledged_at=item["acknowledged_at"],
            expected_end_date=item.get("expected_end_date"),
            resolution_notes=item.get("resolution_notes"),
            related_feedback_ids=item.get("related_feedback_ids", []),
            ttl=int(item["ttl"]) if "ttl" in item else None,
        )