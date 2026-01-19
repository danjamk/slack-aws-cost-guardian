# Backlog

Future enhancements and features for Slack AWS Cost Guardian.

## High Priority

### AWS Budgets Integration ([#2](https://github.com/danjamk/slack-aws-cost-guardian/issues/2))
Integrate with AWS Budgets API to leverage existing budget configurations and alerts.

**Goals:**
- Read budget definitions from AWS Budgets instead of config file
- Sync budget thresholds and alert preferences
- Consolidate alerting so AWS Budgets and Cost Guardian share the same notification channel
- Avoid duplicate alerts between native AWS Budget notifications and Cost Guardian

**Considerations:**
- AWS Budgets already has SNS-based alerting - decide whether to replace or augment
- May need to disable native Budget alerts to avoid duplication
- Could import budget history for trend analysis

---

### Agentic Conversations (Thread-based) ([#3](https://github.com/danjamk/slack-aws-cost-guardian/issues/3))
Enable interactive, multi-turn conversations in Slack threads for deeper cost investigation.

**Goals:**
- User clicks "Investigate" button on an anomaly alert
- Bot responds in a thread with initial analysis
- User can ask follow-up questions: "What caused the EC2 spike?", "Show me the last 7 days"
- Bot uses tools to query Cost Explorer, describe resources, check CloudTrail

**Architecture:**
- Slack Events API subscription (replaces simple webhooks)
- Conversation state management (DynamoDB or in-memory)
- Tool-use pattern with LLM (similar to function calling)
- Rate limiting and cost controls on LLM usage

**Tools to implement:**
- `get_cost_breakdown(service, date_range)` - Detailed cost breakdown
- `get_resource_info(resource_id)` - Describe EC2, RDS, etc.
- `get_recent_changes(service, hours)` - CloudTrail events
- `compare_periods(period1, period2)` - Period-over-period analysis

---

## Medium Priority

### CloudWatch Alarms for System Health ([#4](https://github.com/danjamk/slack-aws-cost-guardian/issues/4))
Add operational monitoring for the Cost Guardian system itself.

**Candidates:**
- Lambda error rate alarm
- Lambda duration approaching timeout
- No successful invocations in X hours
- DynamoDB throttling

**Considerations:**
- Daily reports already serve as implicit health check
- Where to send alerts if Slack is broken? (SNS email fallback)

---

### Multi-Account Support ([#5](https://github.com/danjamk/slack-aws-cost-guardian/issues/5))
Monitor costs across multiple AWS accounts (AWS Organizations).

**Goals:**
- Aggregate costs from member accounts
- Per-account anomaly detection
- Cross-account cost allocation tags

**Requirements:**
- Cross-account IAM roles
- Account-aware storage schema
- Account filtering in reports

---

### Cost Optimization Recommendations ([#6](https://github.com/danjamk/slack-aws-cost-guardian/issues/6))
Proactive suggestions for reducing costs based on usage patterns.

**Ideas:**
- Idle resource detection (EC2, RDS, EBS)
- Reserved Instance / Savings Plan coverage gaps
- Right-sizing recommendations
- Unused Elastic IPs, old snapshots

---

### Historical Trend Dashboard ([#7](https://github.com/danjamk/slack-aws-cost-guardian/issues/7))
Web-based dashboard for visualizing cost trends.

**Options:**
- Simple static HTML generated to S3
- CloudWatch dashboard with custom metrics
- Integration with Grafana

---

## Low Priority / Nice to Have

### Slack App Distribution ([#8](https://github.com/danjamk/slack-aws-cost-guardian/issues/8))
Package as installable Slack app for easier onboarding.

- OAuth flow for workspace installation
- App Home tab with configuration
- Slash commands (`/cost today`, `/cost service ec2`)

---

### Email Digest Option ([#9](https://github.com/danjamk/slack-aws-cost-guardian/issues/9))
Alternative delivery channel for users without Slack.

- SES integration
- HTML email templates
- Weekly PDF report attachment

---

### Forecast Accuracy Tracking ([#10](https://github.com/danjamk/slack-aws-cost-guardian/issues/10))
Track how accurate cost forecasts are over time.

- Compare forecasted vs actual at end of month
- Adjust forecasting model based on accuracy
- Surface forecast confidence in reports

---

### Custom Alert Rules ([#11](https://github.com/danjamk/slack-aws-cost-guardian/issues/11))
User-defined alerting beyond standard anomaly detection.

- "Alert if EC2 exceeds $X/day"
- "Alert if any new service appears"
- "Alert if specific tag has costs"

---

## Completed

- [x] Phase 1: Infrastructure (DynamoDB, Lambda, EventBridge)
- [x] Phase 2: LLM Integration (Anthropic/OpenAI with graceful degradation)
- [x] Phase 3: Slack Callbacks (interactive buttons with feedback)
- [x] Phase 4: Daily/Weekly Reports (scheduled summaries with AI insights)
- [x] Budget threshold alerts (80% warning, 100% critical)
- [x] Historical data backfill command
- [x] Configuration validation (`make validate`)