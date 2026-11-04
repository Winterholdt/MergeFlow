"""Modern Agentic CLI Interface for MergeFlow AI-Orchestrator.

Backward-compatible entry-point and standalone launcher for `core.cli.repl.AgentCLI`.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

# Ensure project root is in sys.path when invoked via entrypoint
_project_root = str(Path(__file__).resolve().parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Ensure Windows consoles support UTF-8 characters and emojis without charmap crashes
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from core.cli.events import (
    CLIEvent,
    CLIEventEmitter,
    EventType,
    get_registered_agents,
)
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
from core.orchestrator import run_orchestrator
from core.state_machine import DEFAULT_STATE_FILE


def build_cli_parser() -> argparse.ArgumentParser:
    """Build CLI parser for standalone execution of cli.py."""
    parser = argparse.ArgumentParser(
        prog="agent-cli",
        description="Interactive Agent CLI for MergeFlow AI Orchestrator (Antigravity style).",
    )
    parser.add_argument(
        "-c",
        "--config",
        dest="config_path",
        default="config.yaml",
        help="Path to YAML configuration file.",
    )
    parser.add_argument(
        "-r",
        "--repo",
        dest="repo_path",
        default=".",
        help="Repository root directory.",
    )
    parser.add_argument(
        "-s",
        "--state",
        dest="state_path",
        default=DEFAULT_STATE_FILE,
        help="Workflow state persistence file.",
    )
    parser.add_argument(
        "--debug",
        dest="debug",
        action="store_true",
        help="Enable debug logging output.",
    )
    return parser


def main() -> None:
    """CLI script entry-point."""
    parser = build_cli_parser()
    args = parser.parse_args()
    code = asyncio.run(
        run_cli(
            config_path=args.config_path,
            repo_path=args.repo_path,
            state_path=args.state_path,
            debug=args.debug,
        )
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
