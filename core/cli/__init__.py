"""Interactive and enhanced CLI tooling for MergeFlow orchestrator.

Provides fancy terminal UI, styled logging, prompt_toolkit text input,
interactive settings configuration, and slash commands.
"""

from core.cli.banner import print_banner
from core.cli.events import (
    AgentInfo,
    AgentRegistry,
    CLIEvent,
    CLIEventEmitter,
    EventType,
    get_registered_agents,
    register_agent,
)
from core.cli.formatting import format_phase_breadcrumbs
from core.cli.interactive import run_interactive_session
from core.cli.logger import setup_cli_logging
from core.cli.ui_handler import TerminalUIEventHandler

__all__ = [
    "print_banner",
    "format_phase_breadcrumbs",
    "run_interactive_session",
    "setup_cli_logging",
    "CLIEvent",
    "CLIEventEmitter",
    "EventType",
    "get_registered_agents",
    "register_agent",
    "AgentInfo",
    "AgentRegistry",
    "TerminalUIEventHandler",
]
