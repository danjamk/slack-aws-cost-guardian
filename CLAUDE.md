# Slack AWS Cost Guardian

## Project Overview
AI-powered AWS cost monitoring with Slack integration. Detects spending anomalies, provides intelligent analysis, and delivers actionable insights to Slack.

## Tech Stack
- **Language**: Python 3.12
- **Infrastructure**: AWS CDK v2
- **Storage**: DynamoDB (on-demand)
- **Compute**: AWS Lambda
- **AI**: Claude API (Anthropic), with abstraction for OpenAI
- **Notifications**: Slack webhooks (Phase 1), Slack app (Phase 2)
- **Package Manager**: uv

## Important Rules
- **NEVER deploy infrastructure directly** - always let the user run deployments
- **NEVER execute git commit/push commands** - provide commands for the user to run
- **Always use Makefile commands** instead of CDK commands directly (e.g., `make deploy` not `cdk deploy`)

## Workflow Commands

| User Request | Action |
|--------------|--------|
| "commit", "make a commit" | Run `/commit` skill |
| "PR", "pull request" | Run `/pr` skill |

## Key Files
- `DESIGN.md` - Full system architecture and implementation plan
- `reference/` - Patterns from previous implementation (not committed)
- `src/slack_aws_cost_guardian/` - Main Python package
- `cdk/` - CDK infrastructure code

## Development Commands
```bash
# Activate environment
source .venv/bin/activate

# Install dependencies
uv pip install -e ".[dev,cdk,llm]"

# Run tests
pytest tests/

# Infrastructure (always use make, never cdk directly)
make synth      # Synthesize CloudFormation
make deploy     # Deploy all stacks
make destroy    # Tear down all stacks

# Testing
make test-collect   # Test cost collection (dry run)
make test-alert     # Test with forced anomaly alert
```

## Implementation Phases
We are building Phase 1 first:

1. DynamoDB tables (cost_snapshots, anomaly_feedback, change_log)
2. Cost Collector Lambda + EventBridge schedule
3. Cost collection from AWS Cost Explorer
4. Basic anomaly detection
5. Simple Slack webhook notifications
6. Reference Implementation

## The reference/ directory contains useful patterns from a previous project:
- reference/tools/cost_tools.py - AWS Cost Explorer queries
- reference/tools/infrastructure_tools.py - AWS resource queries
- reference/config.yaml - Configuration structure example

- These should be adapted to the new architecture, not copied directly.