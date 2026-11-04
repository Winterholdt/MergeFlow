"""Interactive REPL shell for MergeFlow CLI, inspired by Antigravity CLI."""

import asyncio
from pathlib import Path
import sys
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from core.cli.banner import print_banner
from core.cli.formatting import format_phase_breadcrumbs
from core.cli.logger import render_phase_header, setup_cli_logging
from core.cli.prompter import CLIPrompter
from core.cli.settings_menu import (
    interactive_resume_menu,
    interactive_settings_menu,
    show_settings_table,
    show_skills_overview,
)
from core.config import AppConfig, load_config
from core.models import WorkflowPhase, WorkflowState
from core.orchestrator import run_orchestrator
from core.skills import SkillManager
from core.state_machine import DEFAULT_STATE_FILE, load_state
from vcs.git_handler import GitHandler


def print_help_table(console: Console) -> None:
    """Print the formatted slash-command cheat sheet."""
    table = Table(
        title="[bold cyan]Verfügbare Befehle & Slash-Commands[/bold cyan]",
        border_style="cyan",
        header_style="bold magenta",
    )
    table.add_column("Befehl", style="bold cyan", width=22)
    table.add_column("Beschreibung", style="white", width=55)

    table.add_row("<Anforderung>", "Startet den vollständigen 5-Phasen-Workflow für die eingegebene Aufgabe")
    table.add_row("/agents", "Zeigt alle registrierten Agenten, Personas, Phasen & Skills an")
    table.add_row("/config, /settings", "Interaktives Einstellungsmenü (Provider, Auto-Merge, Retries)")
    table.add_row("/provider [name]", "Zeigt oder wechselt den aktiven LLM-Provider (antigravity, claude, api, cli)")
    table.add_row("/skills", "Zeigt alle aktiven Domain-Skills sortiert nach Pipeline-Phasen")
    table.add_row("/status", "Zeigt den aktuellen Task-Status und die 5-Phasen-Pipeline-Breadcrumbs")
    table.add_row("/spec", "Zeigt die aktuelle SPEC.md formatiert als Markdown an")
    table.add_row("/diff", "Zeigt den aktuellen Git-Diff zum Default-Branch an")
    table.add_row("/resume [task_id]", "Setzt einen abgebrochenen oder vorherigen Task fort")
    table.add_row("/multiline", "Schaltet mehrzeiligen Eingabemodus um (Abschicken mit ESC+Enter)")
    table.add_row("/clear", "Leert den Terminal-Bildschirm und zeigt das Banner neu an")
    table.add_row("/help, /?", "Zeigt diese Hilfeübersicht an")
    table.add_row("/exit, /quit", "Beendet die MergeFlow CLI")

    console.print(table)


async def run_interactive_session(
    config_path: str = "config.yaml",
    repo_path: str = ".",
    state_path: str = ".orchestrator/state.json",
    console: Console | None = None,
    debug: bool = False,
) -> int:
    """Run the main interactive REPL shell.

    Args:
        config_path: Path to configuration YAML.
        repo_path: Repository root directory.
        state_path: Workflow state JSON file path.
        console: Optional Rich Console.
        debug: Whether to enable debug logging.

    Returns:
        Exit code (0 on normal exit).
    """
    ui_console = console or Console()
    config = load_config(config_path)
    prompter = CLIPrompter()
    skill_manager = SkillManager(config=config.skills)
    git_handler = GitHandler(repo_path=repo_path)

    # Initial banner
    print_banner(console=ui_console, config=config, repo_path=repo_path)

    while True:
        try:
            user_input = prompter.get_input(console=ui_console, prompt_label="mergeflow")
            if not user_input:
                continue

            cmd_lower = user_input.lower().strip()

            # Handle exit commands
            if cmd_lower in ("/exit", "/quit", "exit", "quit", ":q"):
                ui_console.print("[dim]Auf Wiedersehen! 🚀[/dim]")
                return 0

            # Handle clear command
            elif cmd_lower == "/clear":
                ui_console.clear()
                print_banner(console=ui_console, config=config, repo_path=repo_path)
                continue

            # Handle help command
            elif cmd_lower in ("/help", "/?", "help", "?"):
                print_help_table(ui_console)
                continue

            # Handle multiline toggle
            elif cmd_lower == "/multiline":
                prompter.multiline_mode = not prompter.multiline_mode
                status_txt = "[bold green]aktiviert[/bold green] (ESC+Enter zum Absenden)" if prompter.multiline_mode else "[dim yellow]deaktiviert[/dim yellow]"
                ui_console.print(f"Mehrzeiliger Modus: {status_txt}")
                continue

            # Handle config menu
            elif cmd_lower in ("/config", "/settings", "config", "settings"):
                interactive_settings_menu(config=config, console=ui_console, config_path=config_path)
                continue

            # Handle direct config commands
            elif cmd_lower.startswith("/config "):
                parts = user_input.split()
                if len(parts) >= 3 and parts[1].lower() == "auto-merge":
                    val = parts[2].lower() in ("true", "1", "on", "yes", "ja")
                    config.workflow.auto_merge = val
                    ui_console.print(f"[bold green]✓ Auto-Merge auf {val} gesetzt.[/bold green]")
                elif len(parts) >= 3 and parts[1].lower() == "provider":
                    config.providers.default_provider = parts[2].lower()
                    ui_console.print(f"[bold green]✓ Standard-Provider auf {parts[2]} gesetzt.[/bold green]")
                else:
                    show_settings_table(config, ui_console)
                continue

            # Handle provider command
            elif cmd_lower.startswith("/provider"):
                parts = user_input.split()
                if len(parts) == 1:
                    ui_console.print(f"Aktiver Provider: [bold green]{config.providers.default_provider}[/bold green]")
                else:
                    new_provider = parts[1].lower()
                    if new_provider in ("antigravity", "claude", "api", "cli"):
                        config.providers.default_provider = new_provider
                        ui_console.print(f"[bold green]✓ Provider geändert auf: {new_provider}[/bold green]")
                    else:
                        ui_console.print(f"[bold red]Ungültiger Provider '{new_provider}'. Gültig: antigravity, claude, api, cli[/bold red]")
                continue

            # Handle agents overview
            elif cmd_lower in ("/agents", "agents"):
                from core.cli.events import get_registered_agents
                agents = get_registered_agents()
                agents_table = Table(
                    title="[bold magenta]🤖 Registrierte Orchestrator-Agenten & Personas[/bold magenta]",
                    border_style="magenta",
                    header_style="bold cyan",
                    expand=True,
                )
                agents_table.add_column("Agent / Persona", style="bold cyan", no_wrap=True, width=24)
                agents_table.add_column("Phase", style="bold yellow", width=18)
                agents_table.add_column("Rolle", style="green", width=26)
                agents_table.add_column("Beschreibung & Fähigkeiten", style="white")

                for ag in agents:
                    skills_badge = ""
                    if ag.skills:
                        skills_badge = "\n" + " ".join(f"[dim cyan][{s}][/dim cyan]" for s in ag.skills)
                    agents_table.add_row(
                        f"{ag.icon} {ag.name}",
                        ag.phase,
                        ag.role,
                        f"{ag.description}{skills_badge}",
                    )
                ui_console.print(agents_table)
                continue

            # Handle skills overview
            elif cmd_lower in ("/skills", "skills"):
                show_skills_overview(skill_manager, ui_console)
                continue

            # Handle status command
            elif cmd_lower in ("/status", "status"):
                target_state_file = Path(state_path)
                if not target_state_file.is_file():
                    target_state_file = Path("state.json")

                if target_state_file.is_file():
                    try:
                        state = load_state(target_state_file)
                        breadcrumbs = format_phase_breadcrumbs(state.current_phase)
                        ui_console.print(
                            Panel(
                                f"[bold cyan]Task ID:[/bold cyan] {state.task_id}\n"
                                f"[bold cyan]Phase:[/bold cyan] {state.current_phase}\n"
                                f"[bold cyan]Branch:[/bold cyan] {state.branch_name or 'main'}\n"
                                f"[bold cyan]Review Retries:[/bold cyan] {state.review_retry_count}/{config.workflow.review_max_retries}\n"
                                f"[bold cyan]CI Retries:[/bold cyan] {state.ci_retry_count}/{config.workflow.ci_max_retries}\n\n"
                                f"[bold]Pipeline:[/bold]\n{breadcrumbs}",
                                title="[bold green]Aktueller Workflow-Status[/bold green]",
                                border_style="green",
                            )
                        )
                    except Exception as exc:
                        ui_console.print(f"[bold red]Fehler beim Laden des Status:[/bold red] {exc}")
                else:
                    ui_console.print("[dim]Kein aktiver Workflow-Status vorhanden.[/dim]")
                continue

            # Handle spec view
            elif cmd_lower in ("/spec", "spec"):
                spec_file = Path(repo_path) / "SPEC.md"
                if spec_file.is_file():
                    ui_console.print(
                        Panel(
                            Markdown(spec_file.read_text(encoding="utf-8")),
                            title="[bold cyan]SPEC.md Vorschau[/bold cyan]",
                            border_style="cyan",
                        )
                    )
                else:
                    ui_console.print("[yellow]Keine SPEC.md im Repository gefunden.[/yellow]")
                continue

            # Handle diff view
            elif cmd_lower in ("/diff", "diff"):
                diff_text = await git_handler.get_diff(target_branch=config.workflow.default_branch)
                if diff_text.strip():
                    ui_console.print(
                        Panel(
                            diff_text[:4000] + ("\n... (gekürzt)" if len(diff_text) > 4000 else ""),
                            title="[bold yellow]Aktueller Git-Diff[/bold yellow]",
                            border_style="yellow",
                        )
                    )
                else:
                    ui_console.print("[dim]Keine Git-Änderungen zum Default-Branch vorhanden.[/dim]")
                continue

            # Handle resume command
            elif cmd_lower.startswith("/resume"):
                parts = user_input.split()
                req_task_id = parts[1] if len(parts) > 1 else None
                if not req_task_id:
                    req_task_id = interactive_resume_menu(console=ui_console, state_path=state_path)
                    if not req_task_id:
                        continue

                # Run orchestrator in resume mode
                ui_console.print(f"\n[bold cyan]Setze Task '{req_task_id}' fort...[/bold cyan]")
                setup_cli_logging(console=ui_console, debug=debug)
                try:
                    from core.cli.events import CLIEventEmitter
                    from core.cli.ui_handler import TerminalUIEventHandler
                    emitter = CLIEventEmitter()
                    ui_handler = TerminalUIEventHandler(console=ui_console)
                    emitter.subscribe(ui_handler.handle_event)
                    ui_handler.start_spinner(f"[cyan]Setze Task '{req_task_id}' fort...[/cyan]")
                    await run_orchestrator(
                        requirements="",
                        config_path=config_path,
                        repo_path=repo_path,
                        state_path=state_path,
                        console=ui_console,
                        task_id=req_task_id,
                        resume_from_state=True,
                        event_emitter=emitter,
                    )
                    ui_handler.stop_spinner()
                except Exception as exc:
                    ui_console.print(f"[bold red]Fehler beim Fortsetzen:[/bold red] {exc}")
                continue

            # Otherwise, treat as task prompt!
            prompt_text = user_input
            if prompt_text.lower().startswith("/run "):
                prompt_text = prompt_text[5:].strip()

            ui_console.print(f"\n[bold green]Starte Workflow für Anforderung:[/bold green] {prompt_text}\n")
            setup_cli_logging(console=ui_console, debug=debug)

            try:
                from core.cli.events import CLIEventEmitter
                from core.cli.ui_handler import TerminalUIEventHandler
                emitter = CLIEventEmitter()
                ui_handler = TerminalUIEventHandler(console=ui_console)
                emitter.subscribe(ui_handler.handle_event)
                ui_handler.start_spinner("[cyan]Starte Autonomous Multi-Agent Pipeline...[/cyan]")
                state = await run_orchestrator(
                    requirements=prompt_text,
                    config_path=config_path,
                    repo_path=repo_path,
                    state_path=state_path,
                    console=ui_console,
                    auto_merge=config.workflow.auto_merge,
                    provider_type=config.providers.default_provider,
                    event_emitter=emitter,
                )
                ui_handler.stop_spinner()

                breadcrumbs = format_phase_breadcrumbs(state.current_phase)
                commits_cnt = len(getattr(state, "commits_created", state.metadata.get("commits", []) if isinstance(state.metadata, dict) else []))
                if state.current_phase == WorkflowPhase.DONE.value:
                    ui_console.print(
                        Panel(
                            f"[bold green]✓ Alle Phasen erfolgreich abgeschlossen![/bold green]\n\n"
                            f"Pipeline: {breadcrumbs}\n"
                            f"Branch: [bold yellow]{state.branch_name}[/bold yellow]\n"
                            f"Commits: {commits_cnt}\n"
                            f"Persistiert in: [dim]{state_path}[/dim]",
                            title="[bold green]Workflow Erfolgreich[/bold green]",
                            border_style="green",
                        )
                    )
                else:
                    ui_console.print(
                        Panel(
                            f"[bold yellow]Status: {state.current_phase}[/bold yellow]\n\nPipeline: {breadcrumbs}",
                            title="[bold yellow]Workflow Beendet[/bold yellow]",
                            border_style="yellow",
                        )
                    )
            except Exception as exc:
                ui_console.print(f"\n[bold red]Fehler während des Workflows:[/bold red] {exc}")

        except (KeyboardInterrupt, EOFError):
            ui_console.print("\n[dim]Auf Wiedersehen![/dim]")
            return 0
