# Setup Guide

Complete installation and configuration guide for Slack AWS Cost Guardian.

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** package manager
- **AWS CLI** configured with credentials
- **AWS CDK CLI** (`npm install -g aws-cdk`)
- **Slack workspace** where you can create apps

## 1. Clone & Install

```bash
git clone https://github.com/your-org/slack-aws-cost-guardian.git
cd slack-aws-cost-guardian
make setup
```

This creates a virtual environment, installs dependencies, and creates `.env` from the template.

## 2. Create Slack App

Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App** → **From scratch**.

### 2.1 Basic Information

After creating the app:
1. Note your **Signing Secret** (under App Credentials) - you'll need this for `.env`

### 2.2 Incoming Webhooks (for alerts & reports)

1. Go to **Incoming Webhooks** in the sidebar
2. Toggle **Activate Incoming Webhooks** to ON
3. Click **Add New Webhook to Workspace**
4. Select a channel for critical alerts (e.g., `#aws-alerts-critical`)
5. Copy the webhook URL
6. Repeat for a heartbeat channel (e.g., `#aws-monitoring`)

You'll have two webhook URLs for your `.env` file.

### 2.3 Bot User (for @mentions and DMs)

1. Go to **App Home** in the sidebar
2. Under "Your App's Presence in Slack", click **Edit** next to App Display Name
3. Set:
   - Display name: `Cost Guardian` (or your preference)
   - Default username: `guardian`
4. Enable **Always Show My Bot as Online** (optional)

### 2.4 OAuth & Permissions

1. Go to **OAuth & Permissions** in the sidebar
2. Under **Bot Token Scopes**, add these scopes:

| Scope | Purpose |
|-------|---------|
| `app_mentions:read` | Receive @mentions in channels |
| `chat:write` | Send messages and responses |
| `im:history` | Read DM history |
| `im:read` | Access DM channels |
| `im:write` | Send DMs |
| `reactions:write` | Add emoji reactions (optional) |

3. Click **Install to Workspace** (or Reinstall if already installed)
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

### 2.5 Event Subscriptions (for @mentions and DMs)

1. Go to **Event Subscriptions** in the sidebar
2. Toggle **Enable Events** to ON
3. For **Request URL**, you'll need your Lambda URL (get this after deployment):
   ```bash
   make info | grep -i events
   ```
4. Under **Subscribe to bot events**, add:

| Event | Purpose |
|-------|---------|
| `app_mention` | Respond to @guardian in channels |
| `message.im` | Respond to direct messages |

5. Click **Save Changes**

> **Note**: The Request URL verification will fail until you deploy. That's OK - deploy first, then come back and add the URL.

### 2.6 Interactivity (for feedback buttons)

1. Go to **Interactivity & Shortcuts** in the sidebar
2. Toggle **Interactivity** to ON
3. For **Request URL**, use your callback Lambda URL:
   ```bash
   make info | grep -i callback
   ```
4. Click **Save Changes**

## 3. Configure Secrets

Edit `.env` with your values:

```bash
# Required: Deployment
ENV=dev
AWS_REGION=us-east-1

# Required: Slack Webhooks (from step 2.2)
SLACK_WEBHOOK_CRITICAL=https://hooks.slack.com/services/XXX/YYY/ZZZ
SLACK_WEBHOOK_HEARTBEAT=https://hooks.slack.com/services/XXX/YYY/ZZZ

# Required: Slack App (from steps 2.1 and 2.4)
SLACK_SIGNING_SECRET=your-signing-secret
SLACK_BOT_TOKEN=xoxb-your-bot-token

# Recommended: AI Analysis
ANTHROPIC_API_KEY=sk-ant-api03-your-key
```

### Secret Reference

| Variable | Where to find it |
|----------|------------------|
| `SLACK_SIGNING_SECRET` | Slack App → Basic Information → App Credentials |
| `SLACK_BOT_TOKEN` | Slack App → OAuth & Permissions → Bot User OAuth Token |
| `SLACK_WEBHOOK_*` | Slack App → Incoming Webhooks |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/api-keys) |

## 4. Deploy to AWS

```bash
source .venv/bin/activate
make deploy
```

This deploys:
- **DynamoDB table** - Cost snapshots and feedback storage
- **Lambda functions** - Cost collector, Slack callback handler, Events handler
- **EventBridge schedules** - Automated cost collection
- **S3 bucket** - Configuration storage
- **Secrets Manager** - API keys and tokens (synced from `.env`)

### Post-Deployment: Configure Slack URLs

After deployment, get your Lambda URLs:

```bash
make info
```

Then update your Slack App:
1. **Event Subscriptions** → Request URL → paste Events URL
2. **Interactivity** → Request URL → paste Callback URL

Slack will verify each URL with a challenge request.

## 5. Verify & Test

### Validate Configuration

```bash
make validate
```

Checks that all components are correctly configured.

### Test Cost Collection

```bash
# Dry run - collect costs without storing
make test-collect

# Full collection with storage
make test-full
```

### Test Slack Integration

```bash
# Send a test anomaly alert
make test-alert

# Generate daily report
make test-daily

# Generate weekly report
make test-weekly
```

### Test Bot

1. Invite the bot to a channel: `/invite @guardian`
2. Mention it: `@guardian what did we spend yesterday?`
3. Or send a DM to the bot directly

### Backfill Historical Data

For accurate anomaly detection, load historical data:

```bash
make backfill BACKFILL_DAYS=30
```

## 6. Troubleshooting

### Bot doesn't respond

**Check logs:**
```bash
make logs-events
```

**Common issues:**
- Event Subscriptions URL not configured or not verified
- Missing OAuth scopes (reinstall app after adding scopes)
- Bot not invited to channel (for @mentions)

### Bot responds multiple times

Slack retries if no response within 3 seconds. The bot has deduplication, but if you see duplicates:
- Check Lambda cold start time in logs
- This is normal on first invocation after idle period

### "missing_scope" errors in logs

Add the missing scope in Slack App → OAuth & Permissions, then **Reinstall to Workspace**.

### Slack buttons not working

1. Check Interactivity URL is set correctly
2. Verify `SLACK_SIGNING_SECRET` is correct
3. Redeploy: `make deploy`

### No AI insights in reports

Check that `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set:
```bash
make validate
```

### Daily report shows wrong date

The report shows costs from 2 days ago (configurable via `cost_data_lag_days`) because AWS Cost Explorer data takes 24-48 hours to fully populate. This is intentional for accuracy.

## Command Reference

### Deployment

| Command | Description |
|---------|-------------|
| `make setup` | Initial setup (venv, dependencies, .env) |
| `make deploy` | Deploy all stacks |
| `make destroy` | Tear down all stacks |
| `make diff` | Preview changes before deploy |

### Testing

| Command | Description |
|---------|-------------|
| `make test-collect` | Dry-run cost collection |
| `make test-full` | Full collection with storage |
| `make test-alert` | Send test anomaly to Slack |
| `make test-daily` | Generate daily report |
| `make test-weekly` | Generate weekly report |

### Operations

| Command | Description |
|---------|-------------|
| `make logs` | Tail cost collector logs |
| `make logs-events` | Tail bot/events handler logs |
| `make logs-callback` | Tail callback handler logs |
| `make validate` | Verify configuration |
| `make info` | Show deployment info and URLs |

### Data Management

| Command | Description |
|---------|-------------|
| `make backfill BACKFILL_DAYS=N` | Load N days of historical data |
| `make scan-snapshots` | List recent cost snapshots |

## Next Steps

- **Customize AI context**: Edit `config/guardian-context.md` with your infrastructure details
- **Adjust thresholds**: Edit `config/config.yaml` for anomaly detection sensitivity
- **Multi-environment**: Deploy to staging/prod with `make deploy ENV=prod`

See [ARCHITECTURE.md](ARCHITECTURE.md) for technical details.