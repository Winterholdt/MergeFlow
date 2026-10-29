"""Core package for the Autonomous Multi-Agent Orchestrator (MergeFlow)."""

from core.config import (
    APIProviderConfig,
    AppConfig,
    CLIProviderConfig,
    ProvidersConfig,
    WorkflowConfig,
    load_config,
)
from core.discovery import (
    DiscoveryAnalysis,
    DiscoveryCancelledError,
    DiscoveryError,
    gather_repo_context,
    load_discovery_prompt,
    run_discovery_phase,
)
from core.dev_loop import (
    DevActionType,
    DevAgentStep,
    DevLoopError,
    load_developer_prompt,
    run_dev_phase,
)
from core.models import (
    Finding,
    FindingSeverity,
    PipelineCheck,
    PipelineState,
    PipelineStatus,
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
    "PipelineCheck",
    "PipelineState",
    "PipelineStatus",
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
    "DiscoveryAnalysis",
    "DiscoveryError",
    "DiscoveryCancelledError",
    "gather_repo_context",
    "load_discovery_prompt",
    "run_discovery_phase",
    "DevActionType",
    "DevAgentStep",
    "DevLoopError",
    "load_developer_prompt",
    "run_dev_phase",
]
