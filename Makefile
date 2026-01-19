# Slack AWS Cost Guardian - Makefile
# Common commands for development and deployment

.PHONY: help setup test synth diff deploy destroy validate \
        update-context invoke-collector setup-slack setup-llm clean logs \
        test-daily test-weekly backfill test-budget-warning test-budget-critical \
        info info-json info-secrets

# Load environment from .env file (if exists)
# Priority: command line > .env > default
-include .env
export

# Default environment (used if not set in .env or command line)
ENV ?= dev
AWS_REGION ?= us-east-1

# Default backfill days
BACKFILL_DAYS ?= 30

# Colors for output
BLUE := \033[34m
GREEN := \033[32m
YELLOW := \033[33m
BOLD := \033[1m
RESET := \033[0m

help: ## Show this help message
	@echo "$(BLUE)Slack AWS Cost Guardian$(RESET)"
	@echo ""
	@echo "Usage: make $(YELLOW)<target>$(RESET) [ENV=dev|staging|prod]"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf ""} \
		/^##@/ { printf "\n$(BOLD)$(GREEN)%s$(RESET)\n", substr($$0, 5) } \
		/^[a-zA-Z_-]+:.*?##/ { printf "  $(YELLOW)%-22s$(RESET) %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""

##@ Getting Started

setup: ## Set up development environment (run this first, safe to re-run)
	@echo "$(BLUE)Setting up development environment...$(RESET)"
	@echo ""
	@# Check Python version
	@python3 --version | grep -q "3.1[2-9]" && \
		echo "$(GREEN)✓ Python version OK$(RESET)" || \
		echo "$(YELLOW)⚠ Python 3.12+ recommended (found: $$(python3 --version))$(RESET)"
	@# Create venv if missing
	@if [ ! -d .venv ]; then \
		echo "Creating virtual environment..." && \
		uv venv && \
		echo "$(GREEN)✓ Virtual environment created$(RESET)"; \
	else \
		echo "$(GREEN)✓ Virtual environment exists$(RESET)"; \
	fi
	@# Install dependencies
	@echo "Installing dependencies..."
	@uv pip install -e ".[dev,cdk,llm]" -q && \
		echo "$(GREEN)✓ Dependencies installed$(RESET)"
	@# Setup .env file
	@if [ ! -f .env ] && [ -f .env.example ]; then \
		cp .env.example .env && \
		echo "$(YELLOW)✓ Created .env from template - edit with your values$(RESET)"; \
	elif [ -f .env ]; then \
		echo "$(GREEN)✓ .env file exists$(RESET)"; \
	fi
	@# Check AWS credentials
	@if aws sts get-caller-identity >/dev/null 2>&1; then \
		echo "$(GREEN)✓ AWS credentials configured$(RESET)"; \
	else \
		echo "$(YELLOW)⚠ AWS credentials not configured (run: aws configure)$(RESET)"; \
	fi
	@# Check for required .env values
	@if [ -f .env ]; then \
		. ./.env 2>/dev/null; \
		if [ -z "$$SLACK_WEBHOOK_CRITICAL" ] || [ "$$SLACK_WEBHOOK_CRITICAL" = "https://hooks.slack.com/services/YOUR/WEBHOOK/URL" ]; then \
			echo "$(YELLOW)⚠ SLACK_WEBHOOK_CRITICAL not configured in .env$(RESET)"; \
		fi; \
		if [ -z "$$SLACK_WEBHOOK_HEARTBEAT" ] || [ "$$SLACK_WEBHOOK_HEARTBEAT" = "https://hooks.slack.com/services/YOUR/WEBHOOK/URL" ]; then \
			echo "$(YELLOW)⚠ SLACK_WEBHOOK_HEARTBEAT not configured in .env$(RESET)"; \
		fi; \
	fi
	@echo ""
	@echo "$(GREEN)Setup complete!$(RESET)"
	@echo "Next steps:"
	@echo "  1. source .venv/bin/activate"
	@echo "  2. Edit .env with your Slack webhooks and API keys"
	@echo "  3. make deploy"

##@ Infrastructure

synth: ## Synthesize CDK stacks (preview CloudFormation)
	cdk synth --context environment=$(ENV)

diff: ## Show what would change on deploy
	cdk diff --all --context environment=$(ENV)

deploy: ## Deploy all stacks and configure secrets from .env
	@echo "$(BLUE)Deploying Cost Guardian ($(ENV))...$(RESET)"
	cdk deploy --all --context environment=$(ENV) --require-approval never
	@echo ""
	@echo "$(BLUE)Checking guardian context...$(RESET)"
	@$(MAKE) _upload-context-if-missing ENV=$(ENV)
	@echo ""
	@$(MAKE) _configure-secrets-from-env ENV=$(ENV)
	@echo ""
	@echo "$(GREEN)Deployment complete!$(RESET)"

_configure-secrets-from-env:
	@if [ -f .env ]; then \
		. ./.env 2>/dev/null || true; \
	fi && \
	SLACK_SECRET=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianCollector-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`SlackSecretArn`].OutputValue' \
		--output text 2>/dev/null) && \
	LLM_SECRET=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianCollector-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`LLMSecretArn`].OutputValue' \
		--output text 2>/dev/null) && \
	SLACK_CONFIGURED=false && \
	LLM_CONFIGURED=false && \
	if [ -n "$$SLACK_WEBHOOK_CRITICAL" ] && [ -n "$$SLACK_WEBHOOK_HEARTBEAT" ]; then \
		echo "$(BLUE)Configuring Slack secrets from .env...$(RESET)" && \
		aws secretsmanager put-secret-value \
			--secret-id "$$SLACK_SECRET" \
			--secret-string "{\"webhook_url_critical\":\"$$SLACK_WEBHOOK_CRITICAL\",\"webhook_url_heartbeat\":\"$$SLACK_WEBHOOK_HEARTBEAT\",\"signing_secret\":\"$${SLACK_SIGNING_SECRET:-}\"}" >/dev/null && \
		echo "$(GREEN)✓ Slack webhooks configured$(RESET)" && \
		SLACK_CONFIGURED=true; \
		if [ -z "$$SLACK_SIGNING_SECRET" ]; then \
			echo "$(YELLOW)  Note: SLACK_SIGNING_SECRET not set (button clicks won't work)$(RESET)"; \
		fi; \
	fi && \
	if [ -n "$$ANTHROPIC_API_KEY" ] || [ -n "$$OPENAI_API_KEY" ]; then \
		echo "$(BLUE)Configuring LLM API key from .env...$(RESET)" && \
		aws secretsmanager put-secret-value \
			--secret-id "$$LLM_SECRET" \
			--secret-string "{\"anthropic_api_key\":\"$${ANTHROPIC_API_KEY:-}\",\"openai_api_key\":\"$${OPENAI_API_KEY:-}\"}" >/dev/null && \
		echo "$(GREEN)✓ LLM API key configured$(RESET)" && \
		LLM_CONFIGURED=true; \
	fi && \
	if [ "$$SLACK_CONFIGURED" = "false" ]; then \
		echo "" && \
		echo "$(YELLOW)⚠ Slack webhooks not configured$(RESET)" && \
		echo "  Run: make setup-slack" && \
		echo "  Or add SLACK_WEBHOOK_CRITICAL and SLACK_WEBHOOK_HEARTBEAT to .env"; \
	fi && \
	if [ "$$LLM_CONFIGURED" = "false" ]; then \
		echo "" && \
		echo "$(YELLOW)⚠ LLM API key not configured (AI analysis disabled)$(RESET)" && \
		echo "  Run: make setup-llm" && \
		echo "  Or add ANTHROPIC_API_KEY to .env"; \
	fi

destroy: ## Destroy all stacks (CAUTION: deletes everything)
	@echo "$(YELLOW)WARNING: This will destroy all Cost Guardian resources for $(ENV)$(RESET)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ]
	cdk destroy --all --context environment=$(ENV) --force

##@ Configuration

setup-slack: ## Configure Slack webhook URLs (from .env or interactive)
	@echo "$(BLUE)Setting up Slack webhooks...$(RESET)"
	@SECRET_ARN=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianCollector-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`SlackSecretArn`].OutputValue' \
		--output text 2>/dev/null) && \
	if [ -z "$$SECRET_ARN" ]; then \
		echo "$(YELLOW)Stack not deployed yet. Run 'make deploy' first.$(RESET)"; \
		exit 1; \
	fi && \
	if [ -f .env ]; then \
		. ./.env 2>/dev/null || true; \
	fi && \
	if [ -n "$$SLACK_WEBHOOK_CRITICAL" ] && [ -n "$$SLACK_WEBHOOK_HEARTBEAT" ]; then \
		echo "$(GREEN)Found Slack webhooks in .env$(RESET)" && \
		CRITICAL_URL="$$SLACK_WEBHOOK_CRITICAL" && \
		HEARTBEAT_URL="$$SLACK_WEBHOOK_HEARTBEAT"; \
	else \
		echo "" && \
		echo "You need two Slack webhook URLs:" && \
		echo "  1. Critical alerts channel (e.g., #aws-alerts-critical)" && \
		echo "  2. General/heartbeat channel (e.g., #aws-alerts-general)" && \
		echo "" && \
		echo "Create webhooks at: https://api.slack.com/apps -> Incoming Webhooks" && \
		echo "" && \
		read -p "Critical channel webhook URL: " CRITICAL_URL && \
		read -p "Heartbeat channel webhook URL: " HEARTBEAT_URL && \
		echo "" && \
		echo "$(YELLOW)Tip: Add these to .env for easier future deployments:$(RESET)" && \
		echo "  SLACK_WEBHOOK_CRITICAL=$$CRITICAL_URL" && \
		echo "  SLACK_WEBHOOK_HEARTBEAT=$$HEARTBEAT_URL" && \
		echo ""; \
	fi && \
	aws secretsmanager put-secret-value \
		--secret-id "$$SECRET_ARN" \
		--secret-string "{\"webhook_url_critical\":\"$$CRITICAL_URL\",\"webhook_url_heartbeat\":\"$$HEARTBEAT_URL\"}" && \
	echo "$(GREEN)Slack webhooks configured!$(RESET)"

setup-llm: ## Configure LLM API key (from .env or shows instructions)
	@echo "$(BLUE)Setting up LLM API key...$(RESET)"
	@SECRET_ARN=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianCollector-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`LLMSecretArn`].OutputValue' \
		--output text 2>/dev/null) && \
	if [ -z "$$SECRET_ARN" ]; then \
		echo "$(YELLOW)Stack not deployed yet. Run 'make deploy' first.$(RESET)"; \
		exit 1; \
	fi && \
	if [ -f .env ]; then \
		. ./.env 2>/dev/null || true; \
	fi && \
	if [ -n "$$ANTHROPIC_API_KEY" ] || [ -n "$$OPENAI_API_KEY" ]; then \
		ANTHROPIC_KEY=$${ANTHROPIC_API_KEY:-} && \
		OPENAI_KEY=$${OPENAI_API_KEY:-} && \
		echo "$(GREEN)Found LLM API key(s) in .env$(RESET)" && \
		aws secretsmanager put-secret-value \
			--secret-id "$$SECRET_ARN" \
			--secret-string "{\"anthropic_api_key\":\"$$ANTHROPIC_KEY\",\"openai_api_key\":\"$$OPENAI_KEY\"}" && \
		echo "$(GREEN)LLM API key configured!$(RESET)"; \
	else \
		echo "" && \
		echo "$(YELLOW)No LLM API key found in .env$(RESET)" && \
		echo "" && \
		echo "To enable AI-powered analysis, either:" && \
		echo "" && \
		echo "  1. Add to .env and re-run this command:" && \
		echo "     ANTHROPIC_API_KEY=sk-ant-api03-your-key-here" && \
		echo "" && \
		echo "  2. Or set manually:" && \
		echo "     aws secretsmanager put-secret-value \\" && \
		echo "       --secret-id $$SECRET_ARN \\" && \
		echo "       --secret-string '{\"anthropic_api_key\":\"YOUR_KEY\"}'"; \
	fi

update-context: ## Upload guardian context to S3 (overwrites existing)
	@echo "$(BLUE)Uploading guardian context to S3...$(RESET)"
	@BUCKET=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianStorage-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`ConfigBucketName`].OutputValue' \
		--output text 2>/dev/null) && \
	if [ -z "$$BUCKET" ]; then \
		echo "$(YELLOW)Stack not deployed yet. Run 'make deploy' first.$(RESET)"; \
		exit 1; \
	fi && \
	aws s3 cp config/guardian-context.md s3://$$BUCKET/config/guardian-context.md && \
	echo "$(GREEN)Context uploaded to s3://$$BUCKET/config/guardian-context.md$(RESET)"

_upload-context-if-missing:
	@BUCKET=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianStorage-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`ConfigBucketName`].OutputValue' \
		--output text 2>/dev/null) && \
	if [ -n "$$BUCKET" ]; then \
		EXISTS=$$(aws s3 ls s3://$$BUCKET/config/guardian-context.md 2>/dev/null || true) && \
		if [ -z "$$EXISTS" ]; then \
			echo "$(BLUE)Uploading initial guardian context...$(RESET)" && \
			aws s3 cp config/guardian-context.md s3://$$BUCKET/config/guardian-context.md && \
			echo "$(GREEN)Context uploaded.$(RESET)"; \
		else \
			echo "$(GREEN)Guardian context already exists in S3.$(RESET)"; \
		fi \
	fi

download-context: ## Download current guardian context from S3
	@BUCKET=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianStorage-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`ConfigBucketName`].OutputValue' \
		--output text 2>/dev/null) && \
	if [ -z "$$BUCKET" ]; then \
		echo "$(YELLOW)Stack not deployed yet.$(RESET)"; \
		exit 1; \
	fi && \
	aws s3 cp s3://$$BUCKET/config/guardian-context.md config/guardian-context.md && \
	echo "$(GREEN)Context downloaded to config/guardian-context.md$(RESET)"

##@ Testing Alerts

test-collect: ## Collect costs (dry-run, no storage or Slack)
	@echo "$(BLUE)Running cost collection (dry-run)...$(RESET)"
	@FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	aws lambda invoke \
		--function-name "$$FUNCTION_NAME" \
		--payload '{"test_mode": true, "dry_run": true}' \
		--cli-binary-format raw-in-base64-out \
		--log-type Tail \
		--query 'LogResult' \
		--output text /tmp/collector-response.json | base64 -d && \
	echo "" && \
	echo "$(GREEN)Response:$(RESET)" && \
	cat /tmp/collector-response.json | python3 -m json.tool && \
	echo ""

test-alert: ## Send a test anomaly alert to Slack
	@echo "$(BLUE)Sending test anomaly alert to Slack...$(RESET)"
	@FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	aws lambda invoke \
		--function-name "$$FUNCTION_NAME" \
		--payload '{"test_mode": true, "force_anomaly": true, "skip_storage": true}' \
		--cli-binary-format raw-in-base64-out \
		--log-type Tail \
		--query 'LogResult' \
		--output text /tmp/collector-response.json | base64 -d && \
	echo "" && \
	echo "$(GREEN)Response:$(RESET)" && \
	cat /tmp/collector-response.json | python3 -m json.tool && \
	echo ""

test-full: ## Run full collection with storage and Slack (real run)
	@echo "$(YELLOW)Running FULL cost collection (will store data and send alerts)...$(RESET)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ]
	@FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	aws lambda invoke \
		--function-name "$$FUNCTION_NAME" \
		--payload '{"test_mode": true}' \
		--cli-binary-format raw-in-base64-out \
		--log-type Tail \
		--query 'LogResult' \
		--output text /tmp/collector-response.json | base64 -d && \
	echo "" && \
	echo "$(GREEN)Response:$(RESET)" && \
	cat /tmp/collector-response.json | python3 -m json.tool && \
	echo ""

test-daily: ## Send a daily summary report to Slack
	@echo "$(BLUE)Generating daily summary report...$(RESET)"
	@FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	aws lambda invoke \
		--function-name "$$FUNCTION_NAME" \
		--payload '{"report_type": "daily", "test_mode": true}' \
		--cli-binary-format raw-in-base64-out \
		--log-type Tail \
		--query 'LogResult' \
		--output text /tmp/collector-response.json | base64 -d && \
	echo "" && \
	echo "$(GREEN)Response:$(RESET)" && \
	cat /tmp/collector-response.json | python3 -m json.tool && \
	echo ""

test-weekly: ## Send a weekly summary report to Slack
	@echo "$(BLUE)Generating weekly summary report...$(RESET)"
	@FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	aws lambda invoke \
		--function-name "$$FUNCTION_NAME" \
		--payload '{"report_type": "weekly", "test_mode": true}' \
		--cli-binary-format raw-in-base64-out \
		--log-type Tail \
		--query 'LogResult' \
		--output text /tmp/collector-response.json | base64 -d && \
	echo "" && \
	echo "$(GREEN)Response:$(RESET)" && \
	cat /tmp/collector-response.json | python3 -m json.tool && \
	echo ""

test-budget-warning: ## Send a test budget warning alert (80% threshold)
	@echo "$(BLUE)Sending test budget warning alert...$(RESET)"
	@FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	aws lambda invoke \
		--function-name "$$FUNCTION_NAME" \
		--payload '{"test_mode": true, "force_budget_alert": "warning", "skip_storage": true}' \
		--cli-binary-format raw-in-base64-out \
		--log-type Tail \
		--query 'LogResult' \
		--output text /tmp/collector-response.json | base64 -d && \
	echo "" && \
	echo "$(GREEN)Response:$(RESET)" && \
	cat /tmp/collector-response.json | python3 -m json.tool && \
	echo ""

test-budget-critical: ## Send a test budget critical alert (100% threshold)
	@echo "$(BLUE)Sending test budget critical alert...$(RESET)"
	@FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	aws lambda invoke \
		--function-name "$$FUNCTION_NAME" \
		--payload '{"test_mode": true, "force_budget_alert": "critical", "skip_storage": true}' \
		--cli-binary-format raw-in-base64-out \
		--log-type Tail \
		--query 'LogResult' \
		--output text /tmp/collector-response.json | base64 -d && \
	echo "" && \
	echo "$(GREEN)Response:$(RESET)" && \
	cat /tmp/collector-response.json | python3 -m json.tool && \
	echo ""

##@ Data Management

backfill: ## Backfill historical cost data (BACKFILL_DAYS=30)
	@echo "$(BLUE)Backfilling $(BACKFILL_DAYS) days of historical cost data...$(RESET)"
	@echo "$(YELLOW)This queries AWS Cost Explorer and stores snapshots in DynamoDB.$(RESET)"
	@echo "$(YELLOW)Existing data will NOT be overwritten.$(RESET)"
	@echo ""
	@FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	aws lambda invoke \
		--function-name "$$FUNCTION_NAME" \
		--payload '{"backfill_days": $(BACKFILL_DAYS), "test_mode": true}' \
		--cli-binary-format raw-in-base64-out \
		--log-type Tail \
		--query 'LogResult' \
		--output text /tmp/collector-response.json | base64 -d && \
	echo "" && \
	echo "$(GREEN)Response:$(RESET)" && \
	cat /tmp/collector-response.json | python3 -m json.tool && \
	echo ""

scan-snapshots: ## List recent cost snapshots from DynamoDB
	@TABLE_NAME=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianStorage-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`TableName`].OutputValue' \
		--output text 2>/dev/null) && \
	aws dynamodb scan \
		--table-name "$$TABLE_NAME" \
		--filter-expression "begins_with(PK, :pk)" \
		--expression-attribute-values '{":pk":{"S":"SNAPSHOT#"}}' \
		--max-items 10

describe-table: ## Show DynamoDB table info
	@TABLE_NAME=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianStorage-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`TableName`].OutputValue' \
		--output text 2>/dev/null) && \
	aws dynamodb describe-table --table-name "$$TABLE_NAME"

##@ Information

info: ## Display deployment info and status overview
	@python3 scripts/info.py --env $(ENV)

info-json: ## Display deployment info as JSON
	@python3 scripts/info.py --env $(ENV) --json

info-secrets: ## Display deployment info with secrets revealed (use with caution)
	@python3 scripts/info.py --env $(ENV) --show-secrets

##@ Operations

validate: ## Verify deployment is correctly configured
	@echo "$(BLUE)Validating Cost Guardian deployment ($(ENV))...$(RESET)"
	@echo ""
	@ERRORS=0 && \
	\
	echo "$(BOLD)Checking Lambda...$(RESET)" && \
	FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	if aws lambda get-function --function-name "$$FUNCTION_NAME" >/dev/null 2>&1; then \
		echo "  $(GREEN)✓ Lambda function exists$(RESET)"; \
	else \
		echo "  $(YELLOW)✗ Lambda function not found$(RESET)" && \
		ERRORS=$$((ERRORS + 1)); \
	fi && \
	\
	echo "" && \
	echo "$(BOLD)Checking DynamoDB...$(RESET)" && \
	TABLE_NAME=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianStorage-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`TableName`].OutputValue' \
		--output text 2>/dev/null) && \
	if [ -n "$$TABLE_NAME" ] && aws dynamodb describe-table --table-name "$$TABLE_NAME" >/dev/null 2>&1; then \
		echo "  $(GREEN)✓ DynamoDB table accessible$(RESET)"; \
	else \
		echo "  $(YELLOW)✗ DynamoDB table not found$(RESET)" && \
		ERRORS=$$((ERRORS + 1)); \
	fi && \
	\
	echo "" && \
	echo "$(BOLD)Checking S3 context...$(RESET)" && \
	BUCKET=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianStorage-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`ConfigBucketName`].OutputValue' \
		--output text 2>/dev/null) && \
	if [ -n "$$BUCKET" ] && aws s3 ls "s3://$$BUCKET/config/guardian-context.md" >/dev/null 2>&1; then \
		echo "  $(GREEN)✓ Guardian context uploaded$(RESET)"; \
	else \
		echo "  $(YELLOW)✗ Guardian context missing (run: make update-context)$(RESET)" && \
		ERRORS=$$((ERRORS + 1)); \
	fi && \
	\
	echo "" && \
	echo "$(BOLD)Checking Slack secrets...$(RESET)" && \
	SLACK_SECRET=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianCollector-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`SlackSecretArn`].OutputValue' \
		--output text 2>/dev/null) && \
	if [ -n "$$SLACK_SECRET" ]; then \
		SLACK_VALUE=$$(aws secretsmanager get-secret-value --secret-id "$$SLACK_SECRET" --query 'SecretString' --output text 2>/dev/null) && \
		if echo "$$SLACK_VALUE" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('webhook_url_critical') and 'YOUR' not in d.get('webhook_url_critical','YOUR') else 1)" 2>/dev/null; then \
			echo "  $(GREEN)✓ Slack webhooks configured$(RESET)"; \
		else \
			echo "  $(YELLOW)✗ Slack webhooks not configured (run: make setup-slack)$(RESET)" && \
			ERRORS=$$((ERRORS + 1)); \
		fi; \
	else \
		echo "  $(YELLOW)✗ Slack secret not found$(RESET)" && \
		ERRORS=$$((ERRORS + 1)); \
	fi && \
	\
	echo "" && \
	echo "$(BOLD)Checking LLM secrets...$(RESET)" && \
	LLM_SECRET=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianCollector-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`LLMSecretArn`].OutputValue' \
		--output text 2>/dev/null) && \
	if [ -n "$$LLM_SECRET" ]; then \
		LLM_VALUE=$$(aws secretsmanager get-secret-value --secret-id "$$LLM_SECRET" --query 'SecretString' --output text 2>/dev/null) && \
		if echo "$$LLM_VALUE" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('anthropic_api_key') or d.get('openai_api_key') else 1)" 2>/dev/null; then \
			echo "  $(GREEN)✓ LLM API key configured$(RESET)"; \
		else \
			echo "  $(YELLOW)⚠ LLM API key not configured (AI analysis disabled)$(RESET)"; \
		fi; \
	else \
		echo "  $(YELLOW)✗ LLM secret not found$(RESET)" && \
		ERRORS=$$((ERRORS + 1)); \
	fi && \
	\
	echo "" && \
	echo "$(BOLD)Checking EventBridge schedules...$(RESET)" && \
	SCHEDULE_COUNT=$$(aws events list-rules --name-prefix "cost-guardian" --query 'length(Rules)' --output text 2>/dev/null) && \
	if [ "$$SCHEDULE_COUNT" -ge 3 ] 2>/dev/null; then \
		echo "  $(GREEN)✓ EventBridge schedules active ($$SCHEDULE_COUNT rules)$(RESET)"; \
	else \
		echo "  $(YELLOW)✗ EventBridge schedules missing or incomplete$(RESET)" && \
		ERRORS=$$((ERRORS + 1)); \
	fi && \
	\
	echo "" && \
	if [ "$$ERRORS" -eq 0 ]; then \
		echo "$(GREEN)Validation passed! All checks OK.$(RESET)"; \
	else \
		echo "$(YELLOW)Validation found $$ERRORS issue(s). See above for details.$(RESET)"; \
		exit 1; \
	fi

logs: ## Tail the cost collector Lambda logs
	@FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	aws logs tail /aws/lambda/$$FUNCTION_NAME --follow

invoke-collector: ## Manually invoke the cost collector (alias for test-full)
	@$(MAKE) test-full ENV=$(ENV)

##@ Development

test: ## Run unit tests
	pytest tests/ -v

test-cov: ## Run tests with coverage report
	pytest tests/ -v --cov=src/slack_aws_cost_guardian --cov-report=term-missing

clean: ## Clean build artifacts and caches
	rm -rf cdk.out/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete