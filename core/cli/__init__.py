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
from core.cli.logger import setup_cli_logging
from core.cli.repl import (
    AgentCLI,
    print_cli_help_table,
    run_cli,
    run_interactive_session,
    show_agents_table,
    show_diff_preview,
    show_spec_preview,
    show_status_panel,
)
from core.cli.ui_handler import TerminalUIEventHandler

__all__ = [
    "print_banner",
    "format_phase_breadcrumbs",
    "run_interactive_session",
    "run_cli",
    "AgentCLI",
    "setup_cli_logging",
    "CLIEvent",
    "CLIEventEmitter",
    "EventType",
    "get_registered_agents",
    "register_agent",
    "AgentInfo",
    "AgentRegistry",
    "TerminalUIEventHandler",
    "show_agents_table",
    "show_status_panel",
    "show_spec_preview",
    "show_diff_preview",
    "print_cli_help_table",
]
