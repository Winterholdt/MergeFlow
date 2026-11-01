"""Rich-based structured logging and phase progress visualization."""

from datetime import datetime
import logging
from typing import Any

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.cli.formatting import format_phase_breadcrumbs, get_badge_style
from core.models import WorkflowPhase


class OrchestratorLogHandler(logging.Handler):
    """Custom logging handler providing badges, timestamps, and clean formatting."""

    def __init__(self, console: Console, level: int = logging.INFO):
        super().__init__(level=level)
        self.console = console

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            time_str = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

            # Infer component badge from logger name or message content
            badge_text = "[ORCH]"
            badge_style = "bold cyan"

            lname = record.name.lower()
            msg_lower = record.getMessage().lower()

            if "discovery" in lname or "phase 1" in msg_lower or "spec" in msg_lower:
                badge_text, badge_style = get_badge_style("SPEC")
            elif "gatekeeper" in lname or "security" in lname:
                badge_text, badge_style = get_badge_style("GATE")
            elif "dev_loop" in lname or "phase 2" in msg_lower or "coding" in msg_lower:
                badge_text, badge_style = get_badge_style("DEV")
            elif "healing" in lname or "ci_watcher" in lname or "phase 3" in msg_lower:
                badge_text, badge_style = get_badge_style("CI")
            elif "reviewer" in lname or "phase 4" in msg_lower:
                badge_text, badge_style = get_badge_style("REVIEW")
            elif "consensus" in lname or "phase 5" in msg_lower or "merge" in msg_lower:
                badge_text, badge_style = get_badge_style("CONSENSUS")
            elif "git" in lname or "vcs" in lname:
                badge_text, badge_style = get_badge_style("VCS")

            # Format log level
            level_style = "dim"
            if record.levelno >= logging.ERROR:
                level_style = "bold red"
            elif record.levelno >= logging.WARNING:
                level_style = "bold yellow"
            elif record.levelno >= logging.INFO:
                level_style = "green"

            line = Text()
            line.append(f"{time_str} ", style="dim")
            line.append(f"{record.levelname[:4]:4} ", style=level_style)
            line.append(f"{badge_text:20} ", style=badge_style)
            line.append(record.getMessage())

            self.console.print(line)
        except Exception:
            self.handleError(record)


def setup_cli_logging(console: Console, debug: bool = False) -> None:
    """Configure structured Rich logging for the CLI.

    Args:
        console: Active Rich Console instance.
        debug: Whether to enable DEBUG logging level.
    """
    level = logging.DEBUG if debug else logging.INFO

    # Remove existing root handlers to prevent double logging
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = OrchestratorLogHandler(console=console, level=level)
    root.addHandler(handler)
    root.setLevel(level)


def render_phase_header(
    console: Console,
    phase_num: int,
    phase_name: str,
    description: str,
    current_phase: WorkflowPhase | str | None = None,
) -> None:
    """Print an eye-catching phase transition banner with pipeline breadcrumbs.

    Args:
        console: Active Rich Console.
        phase_num: Phase number (1-5).
        phase_name: Name of the phase.
        description: One-line summary of what this phase does.
        current_phase: Optional current WorkflowPhase.
    """
    breadcrumbs = format_phase_breadcrumbs(current_phase or f"PHASE_{phase_num}")

    content = Table.grid(padding=(0, 1))
    content.add_row(f"[bold cyan]Phase {phase_num}: {phase_name}[/bold cyan]")
    content.add_row(f"[dim]{description}[/dim]")
    content.add_row(Text("─" * 60, style="dim"))
    content.add_row(breadcrumbs)

    console.print()
    console.print(
        Panel(
            content,
            border_style="cyan",
            title=f"[bold green]▶ Pipeline Step {phase_num}/5[/bold green]",
            subtitle="[dim]MergeFlow Autonomous Engine[/dim]",
        )
    )
