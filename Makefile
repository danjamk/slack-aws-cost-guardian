# Slack AWS Cost Guardian - Makefile
# Common commands for development and deployment

.PHONY: help install dev-install test lint format synth diff deploy destroy \
        update-context invoke-collector setup-slack clean

# Default environment
ENV ?= dev
AWS_REGION ?= us-east-1

# Colors for output
BLUE := \033[34m
GREEN := \033[32m
YELLOW := \033[33m
RESET := \033[0m

help: ## Show this help message
	@echo "$(BLUE)Slack AWS Cost Guardian$(RESET)"
	@echo ""
	@echo "Usage: make [target] [ENV=dev|staging|prod]"
	@echo ""
	@echo "$(GREEN)Targets:$(RESET)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(RESET) %s\n", $$1, $$2}'

# ============================================================================
# Development
# ============================================================================

install: ## Install production dependencies
	uv pip install -e .

dev-install: ## Install all dependencies (dev, cdk, llm)
	uv pip install -e ".[dev,cdk,llm]"

test: ## Run tests
	pytest tests/ -v

test-cov: ## Run tests with coverage
	pytest tests/ -v --cov=src/slack_aws_cost_guardian --cov-report=term-missing

lint: ## Run linters (ruff, mypy)
	ruff check src/ tests/
	mypy src/

format: ## Format code with black and ruff
	black src/ tests/
	ruff check --fix src/ tests/

clean: ## Clean build artifacts
	rm -rf cdk.out/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

# ============================================================================
# CDK Commands
# ============================================================================

synth: ## Synthesize CDK stacks
	cdk synth --context environment=$(ENV)

diff: ## Show CDK diff
	cdk diff --all --context environment=$(ENV)

deploy: ## Deploy all stacks and upload context if needed
	@echo "$(BLUE)Deploying Cost Guardian ($(ENV))...$(RESET)"
	cdk deploy --all --context environment=$(ENV) --require-approval never
	@echo "$(BLUE)Checking guardian context...$(RESET)"
	@$(MAKE) _upload-context-if-missing ENV=$(ENV)
	@echo "$(GREEN)Deployment complete!$(RESET)"
	@echo ""
	@echo "$(YELLOW)Next steps:$(RESET)"
	@echo "1. Update Slack webhook URLs in Secrets Manager:"
	@echo "   make setup-slack ENV=$(ENV)"
	@echo ""
	@echo "2. Customize your guardian context:"
	@echo "   Edit config/guardian-context.md"
	@echo "   make update-context ENV=$(ENV)"

destroy: ## Destroy all stacks
	@echo "$(YELLOW)WARNING: This will destroy all Cost Guardian resources for $(ENV)$(RESET)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ]
	cdk destroy --all --context environment=$(ENV) --force

# ============================================================================
# Configuration Management
# ============================================================================

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

# ============================================================================
# Slack Setup
# ============================================================================

setup-slack: ## Interactive setup for Slack webhook URLs
	@echo "$(BLUE)Setting up Slack webhooks...$(RESET)"
	@echo ""
	@echo "You need two Slack webhook URLs:"
	@echo "  1. Critical alerts channel (e.g., #aws-alerts-critical)"
	@echo "  2. General/heartbeat channel (e.g., #aws-alerts-general)"
	@echo ""
	@echo "Create webhooks at: https://api.slack.com/apps -> Incoming Webhooks"
	@echo ""
	@read -p "Critical channel webhook URL: " CRITICAL_URL && \
	read -p "Heartbeat channel webhook URL: " HEARTBEAT_URL && \
	SECRET_ARN=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianCollector-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`SlackSecretArn`].OutputValue' \
		--output text 2>/dev/null) && \
	if [ -z "$$SECRET_ARN" ]; then \
		echo "$(YELLOW)Stack not deployed yet. Run 'make deploy' first.$(RESET)"; \
		exit 1; \
	fi && \
	aws secretsmanager put-secret-value \
		--secret-id "$$SECRET_ARN" \
		--secret-string "{\"webhook_url_critical\":\"$$CRITICAL_URL\",\"webhook_url_heartbeat\":\"$$HEARTBEAT_URL\"}" && \
	echo "$(GREEN)Slack webhooks configured!$(RESET)"

# ============================================================================
# Testing & Debugging
# ============================================================================

test-collect: ## Collect costs (dry-run - no storage, no Slack)
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

invoke-collector: ## Manually invoke the cost collector Lambda (alias for test-full)
	@$(MAKE) test-full ENV=$(ENV)

logs-collector: ## Tail the cost collector Lambda logs
	@FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	aws logs tail /aws/lambda/$$FUNCTION_NAME --follow

logs-collector-recent: ## Show recent collector logs (last 30 min)
	@FUNCTION_NAME="cost-guardian-collector-$(ENV)" && \
	aws logs tail /aws/lambda/$$FUNCTION_NAME --since 30m

describe-table: ## Show DynamoDB table info
	@TABLE_NAME=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianStorage-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`TableName`].OutputValue' \
		--output text 2>/dev/null) && \
	aws dynamodb describe-table --table-name "$$TABLE_NAME"

scan-snapshots: ## List recent cost snapshots
	@TABLE_NAME=$$(aws cloudformation describe-stacks \
		--stack-name CostGuardianStorage-$(ENV) \
		--query 'Stacks[0].Outputs[?OutputKey==`TableName`].OutputValue' \
		--output text 2>/dev/null) && \
	aws dynamodb scan \
		--table-name "$$TABLE_NAME" \
		--filter-expression "begins_with(PK, :pk)" \
		--expression-attribute-values '{":pk":{"S":"SNAPSHOT#"}}' \
		--max-items 10

# ============================================================================
# CI/CD
# ============================================================================

ci-test: ## Run CI test suite
	pytest tests/ -v --tb=short

ci-lint: ## Run CI linting
	ruff check src/ tests/
	black --check src/ tests/