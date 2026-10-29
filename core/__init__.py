"""Core package for the Autonomous Multi-Agent Orchestrator (MergeFlow)."""

from core.config import (
    APIProviderConfig,
    AppConfig,
    CLIProviderConfig,
    ProvidersConfig,
    WorkflowConfig,
    load_config,
)
from core.models import (
    Finding,
    FindingSeverity,
    ReviewResult,
    Specification,
    WorkflowPhase,
    WorkflowState,
)
from core.provider import (
    APIProvider,
    APIResponseError,
    BaseLLMProvider,
    CLIExecutionError,
    CLIProvider,
    JSONParseError,
    ProviderError,
    ProviderTimeoutError,
    get_provider,
)

__version__ = "0.1.0"

__all__ = [
    "AppConfig",
    "APIProviderConfig",
    "CLIProviderConfig",
    "ProvidersConfig",
    "WorkflowConfig",
    "load_config",
    "Finding",
    "FindingSeverity",
    "ReviewResult",
    "Specification",
    "WorkflowPhase",
    "WorkflowState",
    "BaseLLMProvider",
    "CLIProvider",
    "APIProvider",
    "get_provider",
    "ProviderError",
    "ProviderTimeoutError",
    "CLIExecutionError",
    "APIResponseError",
    "JSONParseError",
]
