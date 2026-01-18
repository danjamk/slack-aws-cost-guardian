# AWS Cost Monitor - System Design Document

## Executive Summary

A lightweight, AI-powered AWS cost monitoring system that provides intelligent analysis of spending patterns, detects anomalies, and enables interactive feedback through Slack. Designed to be cost-effective for individuals and startups while scaling to larger organizations.

**Target Cost**: $5-15/month for small deployments, scaling with usage.

---

## Goals & Requirements

### Primary Goals
1. **Daily/Weekly Cost Reporting** - Automated summaries with budget comparison
2. **Anomaly Detection** - AI-powered identification of unusual spending patterns
3. **Interactive Feedback Loop** - Slack-based acknowledgment of expected/unexpected changes
4. **Historical Context** - Learn from past feedback to improve future analysis
5. **Multi-Provider Ready** - Extensible to Claude API, Databricks, etc.

### Non-Functional Requirements
- **Cost Efficiency** - Minimal infrastructure cost
- **Reliability** - Handle AWS service delays gracefully (Cost Explorer has 8-24hr lag)
- **Security** - Least-privilege IAM, secrets management
- **Extensibility** - Clean abstractions for LLM providers, notification channels, cost sources

---

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Account (Management)                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────────┐    ┌──────────────────────────┐  │
│  │  EventBridge │───▶│  Cost Collector  │───▶│      DynamoDB            │  │
│  │  (Scheduled) │    │     Lambda       │    │  ┌──────────────────┐   │  │
│  └──────────────┘    └────────┬─────────┘    │  │ cost_snapshots   │   │  │
│                               │              │  │ anomaly_feedback │   │  │
│                               ▼              │  │ change_log       │   │  │
│                      ┌────────────────┐      │  └──────────────────┘   │  │
│                      │ Cost Explorer  │      └──────────────────────────┘  │
│                      │ Budgets API    │                 │                   │
│                      └────────────────┘                 │                   │
│                                                         ▼                   │
│  ┌──────────────┐    ┌──────────────────┐    ┌──────────────────────────┐  │
│  │  SNS Topic   │◀───│  Alert Analyzer  │◀───│   Analysis Engine        │  │
│  │  (Triggers)  │    │     Lambda       │    │  ┌──────────────────┐   │  │
│  └──────────────┘    └────────┬─────────┘    │  │ LLM Abstraction  │   │  │
│                               │              │  │ (Bedrock/Claude/ │   │  │
│                               │              │  │  OpenAI/Azure)   │   │  │
│                               ▼              │  └──────────────────┘   │  │
│                      ┌────────────────┐      └──────────────────────────┘  │
│                      │ Slack Notifier │                                     │
│                      │    Lambda      │                                     │
│                      └────────┬───────┘                                     │
│                               │                                             │
└───────────────────────────────┼─────────────────────────────────────────────┘
                                │
                                ▼
                       ┌────────────────┐
                       │     Slack      │
                       │  ┌──────────┐  │
                       │  │ Webhooks │  │  ◀── Phase 1 (Buttons via Block Kit)
                       │  │ Slack App│  │  ◀── Phase 2 (Threaded conversations)
                       │  └──────────┘  │
                       └────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Trigger |
|-----------|---------------|---------|
| **Cost Collector Lambda** | Fetch costs from AWS, store snapshots, detect anomalies | EventBridge (configurable: hourly to daily) |
| **Alert Analyzer Lambda** | AI analysis of alerts, format messages | SNS (budget alerts) + Cost Collector |
| **Slack Notifier Lambda** | Send messages, handle button callbacks | Alert Analyzer + API Gateway (callbacks) |
| **DynamoDB Tables** | Persist cost history, feedback, change log | Lambda writes |
| **API Gateway** | Receive Slack interaction callbacks | Slack button clicks |

---

## Data Model (DynamoDB)

### Table 1: `cost_snapshots`

Stores periodic cost data for trend analysis.

```
Primary Key: PK = "SNAPSHOT#{date}" (e.g., "SNAPSHOT#2024-01-15")
Sort Key:    SK = "HOUR#{hour}#{account_id}" (e.g., "HOUR#14#123456789012")

Attributes:
- snapshot_id: string (UUID)
- timestamp: string (ISO 8601)
- account_id: string
- date: string (YYYY-MM-DD)
- hour: number (0-23)
- period_type: string ("hourly" | "daily" | "weekly")
- total_cost: number (decimal)
- currency: string ("USD")
- cost_by_service: map {service_name: cost}
- cost_by_account: map {account_id: {name: string, cost: number}} (for Orgs)
- budget_status: map {
    monthly_budget: number,
    monthly_spent: number,
    monthly_percent: number,
    daily_budget: number,
    daily_spent: number,
    daily_percent: number
  }
- forecast: map {
    end_of_month: number,
    confidence: string
  }
- anomalies_detected: list [{service, amount, percent_change, severity}]
- ttl: number (Unix timestamp, 90 days for hourly, 2 years for daily)

GSI: date-index
  PK: date (YYYY-MM-DD)
  SK: timestamp
  (For querying all snapshots on a given date)
```

### Table 2: `anomaly_feedback`

Stores user responses to anomaly alerts.

```
Primary Key: PK = "FEEDBACK#{date}" (e.g., "FEEDBACK#2024-01-15")
Sort Key:    SK = "ALERT#{alert_id}" (e.g., "ALERT#abc123")

Attributes:
- feedback_id: string (UUID)
- alert_id: string (links to original alert)
- timestamp: string (ISO 8601)
- date: string (YYYY-MM-DD)
- user_id: string (Slack user ID)
- user_name: string (Slack display name)
- feedback_type: string ("expected" | "unexpected" | "investigating")
- affected_services: list [string]
- cost_impact: number (dollar amount of anomaly)
- explanation: string (user's free-text explanation, optional)
- duration_type: string ("one_time" | "ongoing" | "temporary" | "unknown")
- expected_duration_days: number (optional, for temporary)
- related_link: string (optional, PR/ticket URL)
- original_alert_summary: string (truncated alert for context)
- ai_analysis_summary: string (truncated AI response)
- slack_thread_ts: string (for future thread linking)
- ttl: number (Unix timestamp, 2 years)

GSI: user-index
  PK: user_id
  SK: timestamp
  (For querying feedback by user)

GSI: service-index
  PK: affected_services[0] (first service, for common queries)
  SK: timestamp
  (For querying feedback by service)
```

### Table 3: `change_log`

Tracks acknowledged ongoing cost changes for AI context.

```
Primary Key: PK = "CHANGE#{service}" (e.g., "CHANGE#AmazonEC2")
Sort Key:    SK = "DATE#{date}#{change_id}" (e.g., "DATE#2024-01-15#xyz789")

Attributes:
- change_id: string (UUID)
- service: string (AWS service name)
- timestamp: string (ISO 8601)
- date: string (YYYY-MM-DD)
- change_type: string ("new_service" | "cost_increase" | "cost_decrease" | "usage_pattern")
- status: string ("active" | "resolved" | "expired")
- description: string (user or AI generated)
- baseline_cost: number (cost before change)
- new_cost: number (cost after change)
- percent_change: number
- acknowledged_by: string (user_id)
- acknowledged_at: string (ISO 8601)
- expected_end_date: string (optional, YYYY-MM-DD)
- resolution_notes: string (optional)
- related_feedback_ids: list [string]
- ttl: number (Unix timestamp, auto-expire after expected_end_date + 30 days)

GSI: status-index
  PK: status
  SK: timestamp
  (For querying active changes to include in AI context)
```

### Access Patterns

| Query | Table | Key Condition |
|-------|-------|---------------|
| Get today's cost snapshots | cost_snapshots | PK = "SNAPSHOT#2024-01-15" |
| Get last 7 days of daily snapshots | cost_snapshots | Query with date-index, filter period_type = "daily" |
| Get feedback for an alert | anomaly_feedback | PK = "FEEDBACK#{date}", SK = "ALERT#{alert_id}" |
| Get all active cost changes | change_log | GSI status-index, PK = "active" |
| Get changes for a service | change_log | PK = "CHANGE#{service}" |

### Cost Estimation

- **Storage**: ~1KB per snapshot, ~2KB per feedback, ~1KB per change
- **Writes**: ~4-24 snapshots/day, <10 feedback/day typical
- **Reads**: ~50-100 reads/day for analysis
- **Estimated Cost**: $1-3/month for small deployments

---

## LLM Abstraction Layer

### Interface Design

```python
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

@dataclass
class LLMConfig:
    """Configuration for LLM providers."""
    provider: str  # "bedrock", "anthropic", "openai", "azure"
    model_id: str
    temperature: float = 0.3
    max_tokens: int = 2000
    # Provider-specific
    api_key: Optional[str] = None  # For direct API calls
    region: Optional[str] = None   # For Bedrock
    endpoint: Optional[str] = None # For Azure

@dataclass
class LLMMessage:
    """Standard message format."""
    role: str  # "system", "user", "assistant"
    content: str

@dataclass
class LLMResponse:
    """Standard response format."""
    content: str
    model: str
    usage: Dict[str, int]  # {"input_tokens": x, "output_tokens": y}
    finish_reason: str
    raw_response: Optional[Any] = None

class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def __init__(self, config: LLMConfig):
        pass

    @abstractmethod
    def chat(self, messages: List[LLMMessage], **kwargs) -> LLMResponse:
        """Send a chat completion request."""
        pass

    @abstractmethod
    def get_available_models(self) -> List[str]:
        """Return list of available models for this provider."""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name."""
        pass

class LLMFactory:
    """Factory for creating LLM provider instances."""

    _providers: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str, provider_class: type):
        cls._providers[name] = provider_class

    @classmethod
    def create(cls, config: LLMConfig) -> LLMProvider:
        if config.provider not in cls._providers:
            raise ValueError(f"Unknown provider: {config.provider}")
        return cls._providers[config.provider](config)

# Register providers
LLMFactory.register("bedrock", BedrockProvider)
LLMFactory.register("anthropic", AnthropicProvider)
LLMFactory.register("openai", OpenAIProvider)
LLMFactory.register("azure", AzureOpenAIProvider)
```

### Provider Implementations

```python
# providers/bedrock.py
class BedrockProvider(LLMProvider):
    provider_name = "bedrock"

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = boto3.client('bedrock-runtime', region_name=config.region)

    def chat(self, messages: List[LLMMessage], **kwargs) -> LLMResponse:
        # Convert to Bedrock format and call API
        ...

# providers/anthropic.py
class AnthropicProvider(LLMProvider):
    provider_name = "anthropic"

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.api_key)

    def chat(self, messages: List[LLMMessage], **kwargs) -> LLMResponse:
        # Convert to Anthropic format and call API
        ...

# providers/openai.py
class OpenAIProvider(LLMProvider):
    provider_name = "openai"

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = openai.OpenAI(api_key=config.api_key)

    def chat(self, messages: List[LLMMessage], **kwargs) -> LLMResponse:
        # Call OpenAI API
        ...
```

### Configuration (config.yaml)

```yaml
llm:
  # Active provider
  provider: "bedrock"  # Options: bedrock, anthropic, openai, azure

  # Provider-specific settings
  bedrock:
    region: "us-east-1"
    model_id: "anthropic.claude-3-5-sonnet-20241022-v2:0"

  anthropic:
    model_id: "claude-sonnet-4-20250514"
    # api_key loaded from secrets

  openai:
    model_id: "gpt-4o"
    # api_key loaded from secrets

  azure:
    endpoint: "https://your-resource.openai.azure.com"
    model_id: "gpt-4o"
    api_version: "2024-02-15-preview"
    # api_key loaded from secrets

  # Common settings
  temperature: 0.3
  max_tokens: 2000
```

---

## Slack Integration

### Phase 1: Webhooks with Interactive Buttons

Uses Slack Block Kit to create rich messages with buttons. No Slack app required.

**Limitation**: Buttons require a public endpoint to receive callbacks.

```
┌─────────────────────────────────────────────────────────────┐
│ 🚨 Cost Anomaly Detected                                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ **Service**: Amazon EC2                                     │
│ **Change**: +$45.20 (+127% from baseline)                  │
│ **Period**: Last 24 hours                                   │
│                                                             │
│ **AI Analysis**:                                            │
│ Significant increase in EC2 costs detected. This appears   │
│ to be driven by 3 new c5.xlarge instances launched in      │
│ us-east-1. No corresponding auto-scaling events found.     │
│                                                             │
│ **Recommendation**: Verify if these instances are          │
│ intentional. Check for forgotten development resources.    │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ [✓ Expected] [✗ Unexpected] [🔍 Investigating]             │
└─────────────────────────────────────────────────────────────┘
```

**Button Callback Flow**:
1. User clicks button
2. Slack sends POST to API Gateway endpoint
3. Lambda processes callback:
   - Records feedback in DynamoDB
   - Updates Slack message to show acknowledgment
   - If "Expected", prompts for optional explanation (modal)

### Phase 2: Slack App with Threads

Full Slack app enables:
- Threaded conversations for context
- Slash commands (`/cost today`, `/cost forecast`)
- Home tab with dashboard
- Direct messages for personal alerts

**Migration Path**: Phase 1 webhooks continue working alongside Phase 2 app.

### Slack Message Formatting

```python
def format_anomaly_alert(anomaly: dict, analysis: str) -> dict:
    """Format anomaly alert as Slack Block Kit message."""

    severity_emoji = {
        "critical": "🚨",
        "warning": "⚠️",
        "info": "ℹ️"
    }

    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity_emoji[anomaly['severity']]} Cost Anomaly Detected"
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Service*\n{anomaly['service']}"},
                    {"type": "mrkdwn", "text": f"*Change*\n+${anomaly['amount']:.2f} ({anomaly['percent_change']:+.0f}%)"},
                    {"type": "mrkdwn", "text": f"*Period*\n{anomaly['period']}"},
                    {"type": "mrkdwn", "text": f"*Severity*\n{anomaly['severity'].title()}"}
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*AI Analysis*\n{analysis}"
                }
            },
            {
                "type": "actions",
                "block_id": f"anomaly_feedback_{anomaly['id']}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✓ Expected"},
                        "style": "primary",
                        "action_id": "feedback_expected",
                        "value": anomaly['id']
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✗ Unexpected"},
                        "style": "danger",
                        "action_id": "feedback_unexpected",
                        "value": anomaly['id']
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🔍 Investigating"},
                        "action_id": "feedback_investigating",
                        "value": anomaly['id']
                    }
                ]
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Alert ID: `{anomaly['id'][:8]}` | Generated at {anomaly['timestamp']}"
                    }
                ]
            }
        ]
    }
```

---

## Anomaly Detection Strategy

### Multi-Signal Approach

1. **Absolute Threshold** - Cost exceeds configured limit
2. **Percentage Change** - Cost increased/decreased by X% vs baseline
3. **Standard Deviation** - Cost deviates by N standard deviations from rolling average
4. **New Service** - Service appears that wasn't in previous period
5. **Forecast Deviation** - Projected end-of-month exceeds budget by threshold

### Baseline Calculation

```python
def calculate_baseline(snapshots: List[dict], service: str) -> dict:
    """
    Calculate baseline for anomaly detection.
    Uses weighted average: recent days weighted more heavily.
    """
    if not snapshots:
        return {"mean": 0, "std": 0, "trend": 0}

    costs = [s.get('cost_by_service', {}).get(service, 0) for s in snapshots]

    # Weighted average (exponential decay, recent = higher weight)
    weights = [0.9 ** i for i in range(len(costs))]
    weights.reverse()  # Most recent gets highest weight

    weighted_mean = sum(c * w for c, w in zip(costs, weights)) / sum(weights)

    # Standard deviation (unweighted for simplicity)
    std = statistics.stdev(costs) if len(costs) > 1 else 0

    # Trend (simple linear regression slope)
    if len(costs) > 2:
        x = list(range(len(costs)))
        slope, _ = statistics.linear_regression(x, costs)
        trend = slope
    else:
        trend = 0

    return {
        "mean": weighted_mean,
        "std": std,
        "trend": trend
    }
```

### Configuration

```yaml
anomaly_detection:
  enabled: true

  # Thresholds
  absolute_threshold: 100  # Alert if any service costs > $100/day
  percent_change_threshold: 50  # Alert if service cost changes > 50%
  std_deviation_threshold: 2.5  # Alert if cost > 2.5 std dev from mean

  # Baseline settings
  baseline_days: 14  # Use last 14 days for baseline calculation
  minimum_cost_for_anomaly: 5  # Ignore anomalies under $5 (noise reduction)

  # New service detection
  alert_on_new_services: true
  new_service_minimum_cost: 1  # Alert if new service > $1

  # Forecast
  forecast_budget_threshold: 110  # Alert if forecast > 110% of budget
```

---

## Cost Collection Strategy

### Data Sources

1. **AWS Cost Explorer** - Primary source for cost data
   - Granularity: Daily (hourly available but expensive)
   - Delay: 8-24 hours
   - Dimensions: Service, Account, Region, Usage Type

2. **AWS Budgets** - Budget thresholds and forecasts
   - Real-time budget utilization
   - Built-in forecasting

3. **CloudWatch Metrics** - Billing metrics (us-east-1 only)
   - Near real-time estimated charges
   - Limited granularity

### Collection Schedule

```yaml
cost_collection:
  # Primary collection schedule
  schedule:
    frequency: "4x_daily"  # Options: hourly, 4x_daily, 2x_daily, daily
    hours: [6, 12, 18, 0]  # UTC hours for 4x_daily

  # What to collect
  sources:
    cost_explorer:
      enabled: true
      granularity: "DAILY"
      dimensions: ["SERVICE", "LINKED_ACCOUNT"]

    budgets:
      enabled: true
      include_forecast: true

    cloudwatch_billing:
      enabled: false  # Enable for near-real-time estimates

  # Data retention
  retention:
    hourly_snapshots_days: 7
    daily_snapshots_days: 90
    monthly_snapshots_days: 730  # 2 years
```

### Lambda Implementation

```python
def handler(event, context):
    """Cost collector Lambda handler."""

    config = load_config()

    # 1. Collect cost data
    cost_data = collect_costs(config)

    # 2. Store snapshot
    snapshot = create_snapshot(cost_data)
    store_snapshot(snapshot)

    # 3. Detect anomalies
    historical = get_historical_snapshots(days=config['baseline_days'])
    anomalies = detect_anomalies(cost_data, historical, config)

    # 4. Check for acknowledged changes (reduce false positives)
    active_changes = get_active_changes()
    anomalies = filter_acknowledged_anomalies(anomalies, active_changes)

    # 5. If anomalies found, trigger analysis
    if anomalies:
        trigger_alert_analyzer(anomalies, cost_data)

    # 6. Check if it's time for scheduled report
    if should_send_report(config):
        trigger_scheduled_report(cost_data, historical)

    return {"statusCode": 200, "anomalies_found": len(anomalies)}
```

---

## Report Types

### 1. Daily Summary Report

Sent at configured time (e.g., 8 AM local).

```
┌─────────────────────────────────────────────────────────────┐
│ 📊 Daily Cost Report - January 15, 2024                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ **Yesterday's Spend**: $47.23                               │
│ **Month-to-Date**: $612.45 (68% of $900 budget)            │
│ **Projected Month-End**: $892.30 (99% of budget) ⚠️        │
│                                                             │
│ **Top 5 Services**:                                         │
│ 1. Amazon EC2      $23.45  (50%)                           │
│ 2. Amazon RDS      $12.30  (26%)                           │
│ 3. AWS Lambda       $5.67  (12%)                           │
│ 4. Amazon S3        $3.21   (7%)                           │
│ 5. CloudWatch       $2.60   (5%)                           │
│                                                             │
│ **vs. Yesterday**: +$5.20 (+12%) ↑                         │
│ **vs. Last Week Avg**: -$2.10 (-4%) ↓                      │
│                                                             │
│ 💡 *AI Insight*: EC2 costs increased due to the new        │
│ staging environment. This aligns with the deployment       │
│ noted on Jan 12. Consider scheduling auto-shutdown for     │
│ non-production instances during off-hours.                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2. Weekly Summary Report

Sent on configured day (e.g., Monday).

```
┌─────────────────────────────────────────────────────────────┐
│ 📈 Weekly Cost Report - Week of January 8-14, 2024          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ **This Week's Spend**: $312.45                              │
│ **Last Week's Spend**: $298.20                              │
│ **Change**: +$14.25 (+4.8%)                                 │
│                                                             │
│ **Budget Status**:                                          │
│ ████████████████████░░░░░░░░░░ 68% of monthly budget       │
│                                                             │
│ **Service Trends**:                                         │
│ ↑ Amazon EC2:    +15% ($156.20 → $179.63)                  │
│ ↓ AWS Lambda:    -8%  ($42.30 → $38.92)                    │
│ → Amazon RDS:    +2%  ($85.40 → $87.11)                    │
│ 🆕 Amazon SageMaker: $6.79 (new this week)                 │
│                                                             │
│ **Anomalies This Week**: 2                                  │
│ - Jan 10: EC2 spike (+$23) - Marked as expected ✓          │
│ - Jan 12: SageMaker appeared - Under investigation 🔍      │
│                                                             │
│ **Forecast**: On track to finish month at $892 (99%)       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3. Anomaly Alert (shown earlier)

### 4. Budget Threshold Alert

```
┌─────────────────────────────────────────────────────────────┐
│ 💰 Budget Alert: 80% Threshold Reached                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Your monthly AWS budget has reached 80% utilization.        │
│                                                             │
│ **Budget**: $900.00                                         │
│ **Current Spend**: $720.45 (80%)                           │
│ **Days Remaining**: 16                                      │
│ **Projected Month-End**: $1,024.30 (114% of budget) ⚠️     │
│                                                             │
│ **Top Cost Drivers This Month**:                           │
│ 1. Amazon EC2:  $312.45 (43%)                              │
│ 2. Amazon RDS:  $198.30 (28%)                              │
│ 3. AWS Lambda:  $89.45 (12%)                               │
│                                                             │
│ 💡 *Recommendation*: Based on current trajectory, you      │
│ will exceed your budget by ~$124. Consider reviewing       │
│ EC2 instance utilization - 3 instances show <10% CPU       │
│ usage over the past week.                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Security Design

### IAM Permissions (Least Privilege)

```yaml
# Cost Collector Lambda
CostCollectorRole:
  - ce:GetCostAndUsage
  - ce:GetCostForecast
  - budgets:ViewBudget
  - budgets:DescribeBudgets
  - cloudwatch:GetMetricData (for billing metrics)
  - dynamodb:PutItem (cost_snapshots table only)
  - dynamodb:Query (cost_snapshots table only)
  - sns:Publish (to internal topics only)

# Alert Analyzer Lambda
AlertAnalyzerRole:
  - dynamodb:Query (all tables, read)
  - dynamodb:PutItem (anomaly_feedback, change_log)
  - bedrock:InvokeModel (specific models only)
  - secretsmanager:GetSecretValue (specific secrets)
  - sns:Publish

# Slack Notifier Lambda
SlackNotifierRole:
  - dynamodb:UpdateItem (anomaly_feedback)
  - dynamodb:PutItem (change_log)
  - secretsmanager:GetSecretValue (slack webhook)
```

### Secrets Management

```
AWS Secrets Manager:
├── aws-cost-monitor/slack-config
│   ├── webhook_url_critical
│   ├── webhook_url_heartbeat
│   └── signing_secret (for verifying Slack callbacks)
├── aws-cost-monitor/llm-keys (optional)
│   ├── anthropic_api_key
│   └── openai_api_key
```

---

## Implementation Phases

### Phase 1: Core Infrastructure (Week 1-2)
**Goal**: Basic cost collection and storage working

- [ ] Create new repository with clean structure
- [ ] Set up CDK infrastructure:
  - [ ] DynamoDB tables (cost_snapshots, anomaly_feedback, change_log)
  - [ ] Cost Collector Lambda
  - [ ] EventBridge schedule
  - [ ] IAM roles
- [ ] Implement cost collection from Cost Explorer
- [ ] Store snapshots in DynamoDB
- [ ] Basic anomaly detection (threshold-based)
- [ ] Simple Slack webhook notifications

**Deliverable**: System collects costs and sends basic alerts

### Phase 2: AI Analysis (Week 2-3)
**Goal**: Intelligent analysis of cost data

- [ ] Implement LLM abstraction layer
- [ ] Port existing LangGraph workflow (from current repo)
- [ ] Build AI analysis prompts for:
  - [ ] Anomaly explanation
  - [ ] Cost optimization suggestions
  - [ ] Trend identification
- [ ] Integrate with Bedrock
- [ ] Add support for direct Anthropic API

**Deliverable**: AI-powered analysis in Slack messages

### Phase 3: Interactive Feedback (Week 3-4)
**Goal**: Slack buttons and feedback storage

- [ ] Add API Gateway for Slack callbacks
- [ ] Implement Slack Block Kit messages with buttons
- [ ] Build feedback Lambda handler
- [ ] Store feedback in DynamoDB
- [ ] Update AI prompts to include historical feedback context
- [ ] Implement change_log for ongoing acknowledged changes

**Deliverable**: Users can acknowledge anomalies via Slack

### Phase 4: Reporting & Polish (Week 4-5)
**Goal**: Scheduled reports and production readiness

- [ ] Daily summary report
- [ ] Weekly summary report
- [ ] Budget threshold alerts
- [ ] Forecast integration
- [ ] Configuration validation
- [ ] Error handling and retry logic
- [ ] CloudWatch alarms for system health
- [ ] Documentation

**Deliverable**: Production-ready system

### Phase 5: Extensions (Future)
- [ ] Slack app with threads
- [ ] OpenAI/Azure provider support
- [ ] Multi-provider cost monitoring (Claude API, Databricks)
- [ ] Cost allocation tags analysis
- [ ] Reserved instance / Savings Plans recommendations

---

## Project Structure (New Repo)

```
aws-cost-monitor/
├── README.md
├── LICENSE (MIT)
├── CONTRIBUTING.md
├── .gitignore
├── .env.example
├── pyproject.toml              # Modern Python packaging
├── requirements.txt            # Pinned dependencies
├── requirements-dev.txt        # Dev/test dependencies
│
├── cdk/
│   ├── app.py                  # CDK entry point
│   ├── config.py               # Configuration loader
│   └── stacks/
│       ├── __init__.py
│       ├── storage_stack.py    # DynamoDB tables
│       ├── collector_stack.py  # Cost collector Lambda
│       ├── analyzer_stack.py   # AI analyzer Lambda
│       ├── notifier_stack.py   # Slack notifier + API Gateway
│       └── monitoring_stack.py # CloudWatch dashboard
│
├── src/
│   ├── __init__.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── loader.py           # Config loading and validation
│   │   └── schema.py           # Config schema (pydantic)
│   │
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── base.py             # Abstract collector
│   │   ├── aws_cost_explorer.py
│   │   ├── aws_budgets.py
│   │   └── cloudwatch_billing.py
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── dynamodb.py         # DynamoDB operations
│   │   └── models.py           # Data models
│   │
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── anomaly_detector.py
│   │   ├── baseline.py
│   │   └── forecaster.py
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py             # Abstract LLM provider
│   │   ├── factory.py          # Provider factory
│   │   ├── providers/
│   │   │   ├── __init__.py
│   │   │   ├── bedrock.py
│   │   │   ├── anthropic.py
│   │   │   ├── openai.py
│   │   │   └── azure.py
│   │   └── prompts/
│   │       ├── __init__.py
│   │       ├── anomaly_analysis.py
│   │       ├── daily_report.py
│   │       └── weekly_report.py
│   │
│   ├── notifications/
│   │   ├── __init__.py
│   │   ├── base.py             # Abstract notifier
│   │   ├── slack/
│   │   │   ├── __init__.py
│   │   │   ├── webhook.py      # Webhook sender
│   │   │   ├── formatter.py    # Block Kit formatting
│   │   │   └── callback.py     # Button callback handler
│   │   └── email.py            # Future: email notifications
│   │
│   └── handlers/
│       ├── __init__.py
│       ├── cost_collector.py   # Lambda handler
│       ├── alert_analyzer.py   # Lambda handler
│       └── slack_callback.py   # Lambda handler (API Gateway)
│
├── config/
│   ├── config.yaml             # Default configuration
│   ├── config.dev.yaml         # Development overrides
│   └── config.prod.yaml        # Production overrides
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # Pytest fixtures
│   ├── unit/
│   │   ├── test_collectors.py
│   │   ├── test_anomaly.py
│   │   ├── test_llm.py
│   │   └── test_notifications.py
│   ├── integration/
│   │   └── test_end_to_end.py
│   └── fixtures/
│       ├── cost_data.json
│       └── slack_events.json
│
├── scripts/
│   ├── deploy-secrets.py       # Deploy secrets to AWS
│   ├── test-alert.py           # Send test alert
│   └── backfill-history.py     # Backfill historical data
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── CONFIGURATION.md
│   ├── DEPLOYMENT.md
│   └── EXTENDING.md            # Adding new providers
│
└── Makefile
```

---

## Configuration File (Full Example)

```yaml
# config/config.yaml
# AWS Cost Monitor Configuration

project:
  name: "aws-cost-monitor"
  environment: "dev"  # dev, staging, prod
  version: "1.0.0"

aws:
  region: "us-east-1"
  account_id: ""  # Optional, auto-detected

# Cost collection settings
collection:
  schedule:
    frequency: "4x_daily"  # hourly, 4x_daily, 2x_daily, daily
    hours: [6, 12, 18, 0]  # UTC hours (for 4x_daily)
    timezone: "UTC"

  sources:
    cost_explorer:
      enabled: true
      granularity: "DAILY"
      lookback_days: 14

    budgets:
      enabled: true

  retention:
    hourly_days: 7
    daily_days: 90
    monthly_days: 730

# Budget configuration
budgets:
  monthly:
    amount: 900
    currency: "USD"
    warning_threshold: 80
    critical_threshold: 100

  daily:
    amount: 30
    warning_threshold: 100

# Anomaly detection
anomaly_detection:
  enabled: true
  baseline_days: 14

  thresholds:
    absolute: 100          # $100 absolute increase
    percent_change: 50     # 50% change from baseline
    std_deviations: 2.5    # 2.5 std dev from mean

  filters:
    minimum_cost: 5        # Ignore anomalies under $5
    new_service_minimum: 1 # New service threshold

  alert_on_new_services: true

# LLM configuration
llm:
  provider: "bedrock"

  bedrock:
    region: "us-east-1"
    model_id: "anthropic.claude-3-5-sonnet-20241022-v2:0"

  anthropic:
    model_id: "claude-sonnet-4-20250514"

  openai:
    model_id: "gpt-4o"

  temperature: 0.3
  max_tokens: 2000

# Slack configuration
slack:
  enabled: true

  channels:
    critical:
      name: "#aws-alerts-critical"
      webhook_secret_key: "webhook_url_critical"

    heartbeat:
      name: "#aws-alerts-general"
      webhook_secret_key: "webhook_url_heartbeat"

  features:
    interactive_buttons: true
    thread_replies: false  # Phase 2

# Reporting
reports:
  daily:
    enabled: true
    schedule_hour: 8  # UTC
    channel: "heartbeat"
    include_ai_insights: true

  weekly:
    enabled: true
    schedule_day: "monday"
    schedule_hour: 8
    channel: "heartbeat"
    include_ai_insights: true

# Notification routing
routing:
  budget_warning: "heartbeat"
  budget_critical: "critical"
  anomaly_warning: "heartbeat"
  anomaly_critical: "critical"
  daily_report: "heartbeat"
  weekly_report: "heartbeat"

# Tags for resources
tags:
  Project: "aws-cost-monitor"
  Environment: "${project.environment}"
  ManagedBy: "CDK"
```

---

## Questions for You

1. **Repository name**: `aws-cost-monitor` good, or do you want something different?

2. **License**: MIT for public sharing?

3. **Python version**: 3.12 (current) or 3.11 (wider compatibility)?

4. **Packaging**: `pyproject.toml` (modern) or `setup.py` (traditional)?

5. **Testing framework**: pytest (recommended) or unittest?

6. **Should we support email notifications** in Phase 1, or defer to later?
