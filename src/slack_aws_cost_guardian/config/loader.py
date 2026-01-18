"""Configuration loader for Slack AWS Cost Guardian."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError

from slack_aws_cost_guardian.config.schema import Config


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries, with override taking precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _find_config_dir() -> Path:
    """Find the config directory, searching up from current directory."""
    # Check for CONFIG_DIR environment variable first
    if config_dir := os.environ.get("CONFIG_DIR"):
        return Path(config_dir)

    # Search up from current directory
    current = Path.cwd()
    while current != current.parent:
        config_path = current / "config"
        if config_path.is_dir():
            return config_path
        current = current.parent

    # Fall back to ./config
    return Path("config")


def load_config(
    config_path: str | Path | None = None,
    environment: str | None = None,
) -> Config:
    """
    Load configuration from YAML files.

    Loads config.yaml as base, then merges environment-specific overrides
    (e.g., config.dev.yaml, config.prod.yaml).

    Args:
        config_path: Path to config directory. If None, searches for config/ directory.
        environment: Environment name (dev, staging, prod). If None, uses CONFIG_ENV
                    environment variable or defaults to 'dev'.

    Returns:
        Config: Validated configuration object.
    """
    config_dir = Path(config_path) if config_path else _find_config_dir()
    environment = environment or os.environ.get("CONFIG_ENV", "dev")

    # Load base config
    base_config_path = config_dir / "config.yaml"
    config_data: dict = {}

    if base_config_path.exists():
        with open(base_config_path) as f:
            config_data = yaml.safe_load(f) or {}

    # Load environment-specific overrides
    env_config_path = config_dir / f"config.{environment}.yaml"
    if env_config_path.exists():
        with open(env_config_path) as f:
            env_data = yaml.safe_load(f) or {}
            config_data = _deep_merge(config_data, env_data)

    # Override with environment variables
    config_data = _apply_env_overrides(config_data)

    # Set environment in config
    config_data["environment"] = environment

    return Config(**config_data)


def _apply_env_overrides(config_data: dict) -> dict:
    """Apply environment variable overrides to configuration."""
    # Common overrides via environment variables
    env_mappings = {
        "AWS_REGION": ("aws", "region"),
        "AWS_ACCOUNT_ID": ("aws", "account_id"),
        "LLM_PROVIDER": ("llm", "provider"),
        "MONTHLY_BUDGET": ("budgets", "monthly", "amount"),
        "SLACK_ENABLED": ("slack", "enabled"),
    }

    for env_var, path in env_mappings.items():
        if value := os.environ.get(env_var):
            # Navigate to the nested key and set the value
            current = config_data
            for key in path[:-1]:
                current = current.setdefault(key, {})

            # Convert types as needed
            final_key = path[-1]
            if final_key in ("amount",):
                current[final_key] = float(value)
            elif final_key in ("enabled",):
                current[final_key] = value.lower() in ("true", "1", "yes")
            else:
                current[final_key] = value

    return config_data


def load_guardian_context(
    bucket_name: str,
    s3_key: str = "config/guardian-context.md",
    s3_client: boto3.client | None = None,
) -> str:
    """
    Load guardian context from S3.

    Args:
        bucket_name: S3 bucket name.
        s3_key: S3 object key for the context file.
        s3_client: Optional boto3 S3 client. If None, creates a new one.

    Returns:
        str: Guardian context content, or empty string if not found.
    """
    if s3_client is None:
        s3_client = boto3.client("s3")

    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
        return response["Body"].read().decode("utf-8")
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "NoSuchKey":
            # Context file doesn't exist yet - that's OK
            return ""
        raise


@lru_cache(maxsize=1)
def get_cached_config() -> Config:
    """
    Get cached configuration singleton.

    Useful for Lambda handlers to avoid re-loading config on warm starts.
    """
    return load_config()