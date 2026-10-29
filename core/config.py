"""Configuration management for MergeFlow orchestrator.

Handles loading, validating, and managing configuration settings from YAML files
and environment variables using Pydantic Settings.
"""

from pathlib import Path
from typing import Any, Tuple, Type

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class CLIProviderConfig(BaseModel):
    """Configuration for CLI-based LLM providers (e.g., claude -p, antigravity run)."""

    binary_path: str = Field(
        default="claude",
        description="Path or binary executable name for the CLI tool.",
    )
    command_args: list[str] = Field(
        default_factory=lambda: ["-p"],
        description="Default command arguments to pass before the prompt.",
    )
    timeout_seconds: float = Field(
        default=300.0,
        description="Execution timeout in seconds for subprocess calls.",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Optional custom environment variables for subprocess execution.",
    )


class APIProviderConfig(BaseModel):
    """Configuration for API-based LLM providers (OpenAI-compatible / LiteLLM)."""

    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Base URL for the API endpoint.",
    )
    api_key: str | None = Field(
        default=None,
        description="API key or token for authentication.",
    )
    model: str = Field(
        default="gpt-4o",
        description="Default model identifier to query.",
    )
    timeout_seconds: float = Field(
        default=60.0,
        description="HTTP request timeout in seconds.",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum number of retry attempts for failed requests.",
    )


class ProvidersConfig(BaseModel):
    """Container for provider configurations and profiles."""

    default_provider: str = Field(
        default="cli",
        description="Default provider type ('cli' or 'api').",
    )
    cli: CLIProviderConfig = Field(
        default_factory=CLIProviderConfig,
        description="Default CLI provider settings.",
    )
    api: APIProviderConfig = Field(
        default_factory=APIProviderConfig,
        description="Default API provider settings.",
    )
    claude: CLIProviderConfig = Field(
        default_factory=lambda: CLIProviderConfig(binary_path="claude", command_args=["-p"]),
        description="Profile for Claude CLI.",
    )
    antigravity: CLIProviderConfig = Field(
        default_factory=lambda: CLIProviderConfig(binary_path="antigravity", command_args=["run"]),
        description="Profile for Antigravity CLI.",
    )


class WorkflowConfig(BaseModel):
    """Configuration settings controlling autonomous workflow loops."""

    ci_max_retries: int = Field(
        default=3,
        ge=0,
        description="Maximum retry attempts for resolving CI failures.",
    )
    review_max_retries: int = Field(
        default=3,
        ge=0,
        description="Maximum retry attempts for addressing code review findings.",
    )
    default_branch: str = Field(
        default="main",
        description="Default base branch for repository operations.",
    )
    working_directory: str = Field(
        default=".",
        description="Root working directory for repository tasks.",
    )
    auto_merge: bool = Field(
        default=False,
        description="Whether to automatically merge PR/MR when all reviews pass.",
    )


class SkillsConfig(BaseModel):
    """Configuration for modular agent skills."""

    enabled: bool = Field(
        default=True,
        description="Whether modular agent skills injection is enabled.",
    )
    directory: str = Field(
        default="./skills",
        description="Root directory containing modular skill definitions.",
    )
    phases: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "discovery": ["discovery/requirements_engineering.md"],
            "dev": [
                "dev/clean_code_solid.md",
                "dev/test_driven_development.md",
                "dev/conventional_commits.md",
            ],
            "review_security": ["review/security_owasp.md"],
            "review_architecture": ["review/architecture_quality.md"],
            "review_qa": ["review/qa_coverage.md"],
        },
        description="Mapping of workflow phases to list of skill file paths.",
    )


class AppConfig(BaseSettings):
    """Root application configuration schema with YAML and environment variable support."""

    model_config = SettingsConfigDict(
        yaml_file="config.yaml",
        yaml_file_encoding="utf-8",
        env_prefix="MERGEFLOW_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: str = Field(
        default="development",
        description="Application environment (e.g., development, testing, production).",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level threshold.",
    )
    providers: ProvidersConfig = Field(
        default_factory=ProvidersConfig,
        description="LLM provider configurations.",
    )
    workflow: WorkflowConfig = Field(
        default_factory=WorkflowConfig,
        description="Workflow execution parameters.",
    )
    skills: SkillsConfig = Field(
        default_factory=SkillsConfig,
        description="Modular agentic skills configuration.",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """Customise settings sources to load from YAML file, env vars, and init kwargs."""
        yaml_source = YamlConfigSettingsSource(settings_cls)
        return (init_settings, env_settings, yaml_source, file_secret_settings)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AppConfig":
        """Load and validate configuration directly from a specified YAML file path."""
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls.model_validate(data)


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load configuration from a file or fall back to default config.yaml/environment.

    Args:
        path: Optional explicit path to configuration YAML file.

    Returns:
        Validated AppConfig instance.
    """
    if path is not None:
        p = Path(path)
        if p.is_file():
            return AppConfig.from_yaml(p)
        # If the requested path was the default 'config.yaml' and not found in CWD,
        # fallback to the package-level default config.yaml
        if str(path) == "config.yaml":
            package_config = Path(__file__).resolve().parent.parent / "config.yaml"
            if package_config.is_file():
                return AppConfig.from_yaml(package_config)
            return AppConfig()
        raise FileNotFoundError(f"Configuration file not found: {p}")

    default_file = Path("config.yaml")
    if default_file.is_file():
        return AppConfig.from_yaml(default_file)

    package_config = Path(__file__).resolve().parent.parent / "config.yaml"
    if package_config.is_file():
        return AppConfig.from_yaml(package_config)

    return AppConfig()
