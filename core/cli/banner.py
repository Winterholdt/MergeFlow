"""Sleek terminal branding and header banner for MergeFlow CLI."""

from pathlib import Path
import subprocess
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.config import AppConfig


def _get_current_git_branch(repo_path: Path) -> str:
    """Detect current active git branch."""
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        branch = proc.stdout.strip()
        return branch if branch else "main"
    except Exception:
        return "main"


def print_banner(
    console: Console,
    config: AppConfig,
    repo_path: Path | str = ".",
    version: str = "0.1.0",
) -> None:
    """Render the stylish Antigravity-style CLI banner.

    Args:
        console: Active Rich Console instance.
        config: Loaded AppConfig configuration.
        repo_path: Target repository root path.
        version: Package version string.
    """
    repo = Path(repo_path).resolve()
    branch = _get_current_git_branch(repo)

    # Stylized ASCII art header with clean gradient feel
    logo = (
        r"  __  __                     _____ _                 " "\n"
        r" |  \/  |                   |  ___| |                " "\n"
        r" | \  / | ___ _ __ __ _  ___| |_  | | _____      __  " "\n"
        r" | |\/| |/ _ \ '__/ _` |/ _ \  _| | |/ _ \ \ /\ / /  " "\n"
        r" | |  | |  __/ | | (_| |  __/ |   | | (_) \ V  V /   " "\n"
        r" |_|  |_|\___|_|  \__, |\___|_|   |_|\___/ \_/\_/    " "\n"
        r"                   __/ |                             " "\n"
        r"                  |___/                              "
    )

    logo_text = Text(logo, style="bold cyan")

    # Header title & subtitle
    header_text = Text()
    header_text.append(logo_text)
    header_text.append("\n  Autonomous Multi-Agent Development Pipeline (Phases 1-5)\n", style="bold white")
    header_text.append(f"  v{version}  •  Inspired by Antigravity CLI  •  5-Phase SDLC Engine\n", style="dim")

    # Status Grid / Info Bar
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    # Provider & Model details
    provider_name = config.providers.default_provider
    if provider_name == "api":
        model_info = config.providers.api.model
    elif provider_name == "antigravity":
        model_info = f"antigravity ({config.providers.antigravity.binary_path})"
    elif provider_name == "claude":
        model_info = f"claude ({config.providers.claude.binary_path})"
    else:
        model_info = f"cli ({config.providers.cli.binary_path})"

    auto_merge_badge = "[bold green]ON[/bold green]" if config.workflow.auto_merge else "[dim yellow]OFF[/dim yellow]"
    skills_badge = "[bold green]Enabled[/bold green]" if config.skills.enabled else "[dim red]Disabled[/dim red]"

    grid.add_row("Provider:", f"[bold green]{provider_name}[/bold green] ({model_info})", "Repository:", f"[dim]{repo.name}[/dim]")
    grid.add_row("Branch:", f"[bold yellow]{branch}[/bold yellow]", "Auto-Merge:", auto_merge_badge)
    grid.add_row("Skills:", skills_badge, "CI Retries:", str(config.workflow.ci_max_retries))

    panel_content = Table.grid()
    panel_content.add_row(header_text)
    panel_content.add_row(Text("─" * 68, style="dim cyan"))
    panel_content.add_row(grid)

    console.print(
        Panel(
            panel_content,
            border_style="cyan",
            padding=(0, 1),
        )
    )
    console.print(
        "[dim]  Tippe eine Anforderung ein oder nutze Slash-Commands wie [bold cyan]/help[/bold cyan], [bold cyan]/config[/bold cyan], [bold cyan]/skills[/bold cyan], [bold cyan]/resume[/bold cyan] oder [bold cyan]/exit[/bold cyan].[/dim]\n"
    )
