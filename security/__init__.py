"""Security and execution sandbox package for MergeFlow orchestrator.

Provides:
- Policy rule definitions and patterns (allow_block_list)
- Gatekeeper and LLM auditor (gatekeeper)
- Secure async shell runner with timeout and streaming (shell_runner)
"""

from security.allow_block_list import (
    BlockRule,
    FAST_PATH_COMMAND_PATTERNS,
    HARD_BLOCK_RULES,
    SENSITIVE_PATH_PATTERNS,
    check_hard_blocklist,
    check_sensitive_paths,
    is_fast_path_allowed,
)
from security.gatekeeper import (
    AuditLLM,
    Gatekeeper,
    GatekeeperDecision,
    GatekeeperResult,
)
from security.shell_runner import (
    CommandResult,
    ShellRunner,
    run_command,
)

__all__ = [
    "AuditLLM",
    "BlockRule",
    "CommandResult",
    "FAST_PATH_COMMAND_PATTERNS",
    "Gatekeeper",
    "GatekeeperDecision",
    "GatekeeperResult",
    "HARD_BLOCK_RULES",
    "SENSITIVE_PATH_PATTERNS",
    "ShellRunner",
    "check_hard_blocklist",
    "check_sensitive_paths",
    "is_fast_path_allowed",
    "run_command",
]
