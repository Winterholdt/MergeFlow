"""Tests for configuration loading and validation."""

from pathlib import Path
import pytest
from pydantic import ValidationError

from core.config import (
    AppConfig,
    APIProviderConfig,
    CLIProviderConfig,
    WorkflowConfig,
    load_config,
)


def test_default_config_instantiation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify default configuration values."""
    monkeypatch.chdir(tmp_path)
    config = AppConfig()
    assert config.environment == "development"
    assert config.log_level == "INFO"
    assert config.providers.default_provider == "cli"
    assert config.providers.cli.binary_path == "claude"
    assert config.providers.cli.command_args == ["-p"]
    assert config.providers.cli.timeout_seconds == 300.0
    assert config.providers.api.model == "gpt-4o"
    assert config.workflow.ci_max_retries == 3
    assert config.workflow.review_max_retries == 3
    assert config.workflow.auto_merge is False


def test_load_from_valid_yaml(tmp_path: Path):
    """Verify loading settings from custom YAML file."""
    yaml_content = """
environment: "production"
log_level: "DEBUG"
providers:
  default_provider: "api"
  cli:
    binary_path: "custom-cli"
    command_args: ["--prompt"]
    timeout_seconds: 120.0
  api:
    base_url: "https://custom.api.com/v1"
    api_key: "secret-key"
    model: "claude-3-opus"
    timeout_seconds: 45.0
    max_retries: 5
workflow:
  ci_max_retries: 5
  review_max_retries: 2
  default_branch: "develop"
"""
    custom_yaml = tmp_path / "custom_config.yaml"
    custom_yaml.write_text(yaml_content, encoding="utf-8")

    config = load_config(custom_yaml)
    assert config.environment == "production"
    assert config.log_level == "DEBUG"
    assert config.providers.default_provider == "api"
    assert config.providers.cli.binary_path == "custom-cli"
    assert config.providers.cli.command_args == ["--prompt"]
    assert config.providers.cli.timeout_seconds == 120.0
    assert config.providers.api.base_url == "https://custom.api.com/v1"
    assert config.providers.api.api_key == "secret-key"
    assert config.providers.api.model == "claude-3-opus"
    assert config.workflow.ci_max_retries == 5
    assert config.workflow.review_max_retries == 2
    assert config.workflow.default_branch == "develop"


def test_load_non_existent_yaml_raises_error():
    """Verify loading from non-existent file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_config("non_existent_config.yaml")


def test_validation_constraints():
    """Verify validation rules on negative retry counters."""
    with pytest.raises(ValidationError):
        WorkflowConfig(ci_max_retries=-1)

    with pytest.raises(ValidationError):
        WorkflowConfig(review_max_retries=-5)


def test_root_config_yaml_loading():
    """Verify that root config.yaml is loaded and valid."""
    config = load_config("config.yaml")
    assert config.providers.claude.binary_path == "claude"
    assert config.providers.antigravity.binary_path == "antigravity"
    assert config.providers.antigravity.command_args == ["run"]
