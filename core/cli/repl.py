"""Modern Agentic REPL & CLI Interface for MergeFlow AI-Orchestrator.

Provides an Antigravity / Claude Code style interactive terminal REPL with:
- Asynchronous prompt_toolkit REPL (`PromptSession.prompt_async`)
- Input history and autocompletion for slash commands
- Decoupled real-time event listener for live status feedback & spinners
- Markdown rendering, rich tables, and diff previews
- Slash commands (/agents, /status, /spec, /diff, /skills, /config, /provider, /resume, /help, /clear, /exit)
- Subcommand rendering helpers for direct terminal execution
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any

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

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from core.cli.banner import print_banner
from core.cli.events import (
    CLIEvent,
    CLIEventEmitter,
    EventType,
    get_registered_agents,
)
from core.cli.formatting import format_phase_breadcrumbs
from core.cli.logger import setup_cli_logging
from core.cli.settings_menu import (
    interactive_resume_menu,
    interactive_settings_menu,
    show_settings_table,
    show_skills_overview,
)
from core.cli.ui_handler import TerminalUIEventHandler
from core.config import AppConfig, load_config
from core.discovery import DiscoveryCancelledError
from core.models import WorkflowPhase
from core.orchestrator import Orchestrator, run_orchestrator
from core.skills import SkillManager
from core.state_machine import DEFAULT_STATE_FILE, load_state
from vcs.git_handler import GitHandler

# Autocompletion tree for slash-commands
CLI_SLASH_COMMANDS = {
    "/agents": None,
    "/help": None,
    "/?": None,
    "/status": None,
    "/skills": None,
    "/spec": None,
    "/diff": None,
    "/config": {
        "show": None,
        "provider": {"antigravity": None, "claude": None, "api": None, "cli": None},
        "auto-merge": {"true": None, "false": None},
    },
    "/provider": {
        "antigravity": None,
        "claude": None,
        "api": None,
        "cli": None,
    },
    "/resume": None,
    "/clear": None,
    "/exit": None,
    "/quit": None,
}

PROMPT_STYLE = Style.from_dict({
    "prompt-brand": "#00d7ff bold",
    "prompt-arrow": "#af00ff bold",
    "prompt-dim": "#777777",
})


# =============================================================================
# Standalone CLI Display Helpers (reusable in REPL and direct subcommands)
# =============================================================================


def show_agents_table(console: Console) -> None:
    """Print the formatted overview table of all registered agents & personas."""
    agents = get_registered_agents()
    table = Table(
        title="[bold magenta]🤖 Registrierte Orchestrator-Agenten & Personas[/bold magenta]",
        border_style="magenta",
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("Agent / Persona", style="bold cyan", no_wrap=True, width=24)
    table.add_column("Phase", style="bold yellow", width=18)
    table.add_column("Rolle", style="green", width=26)
    table.add_column("Beschreibung & Fähigkeiten", style="white")

    for ag in agents:
        skills_badge = ""
        if ag.skills:
            skills_badge = "\n" + " ".join(f"[dim cyan][{s}][/dim cyan]" for s in ag.skills)
        table.add_row(
            f"{ag.icon} {ag.name}",
            ag.phase,
            ag.role,
            f"{ag.description}{skills_badge}",
        )
    console.print(table)


def show_status_panel(
    console: Console,
    state_path: str = DEFAULT_STATE_FILE,
    config: AppConfig | None = None,
) -> None:
    """Print the formatted workflow state and pipeline breadcrumbs."""
    target_state_file = Path(state_path)
    if not target_state_file.is_file():
        target_state_file = Path("state.json")

    if not target_state_file.is_file():
        console.print("[dim]Kein aktiver Workflow-Status auf der Festplatte gefunden.[/dim]")
        return

    try:
        state = load_state(target_state_file)
        breadcrumbs = format_phase_breadcrumbs(state.current_phase)
        commits_cnt = len(
            getattr(
                state,
                "commits_created",
                state.metadata.get("commits", []) if isinstance(state.metadata, dict) else [],
            )
        )
        review_max = config.workflow.review_max_retries if config else 3
        ci_max = config.workflow.ci_max_retries if config else 3
        console.print(
            Panel(
                f"[bold cyan]Task ID:[/bold cyan] {state.task_id}\n"
                f"[bold cyan]Phase:[/bold cyan] {state.current_phase}\n"
                f"[bold cyan]Branch:[/bold cyan] {state.branch_name or 'main'}\n"
                f"[bold cyan]Commits:[/bold cyan] {commits_cnt}\n"
                f"[bold cyan]Review Retries:[/bold cyan] {state.review_retry_count}/{review_max}\n"
                f"[bold cyan]CI Retries:[/bold cyan] {state.ci_retry_count}/{ci_max}\n\n"
                f"[bold]Pipeline:[/bold]\n{breadcrumbs}",
                title="[bold green]Aktueller Workflow-Status[/bold green]",
                border_style="green",
            )
        )
    except Exception as exc:
        console.print(f"[bold red]Fehler beim Laden des Status:[/bold red] {exc}")


def show_spec_preview(console: Console, repo_path: str = ".") -> None:
    """Print the formatted SPEC.md markdown preview."""
    spec_file = Path(repo_path) / "SPEC.md"
    if spec_file.is_file():
        console.print(
            Panel(
                Markdown(spec_file.read_text(encoding="utf-8")),
                title="[bold cyan]SPEC.md Vorschau[/bold cyan]",
                border_style="cyan",
            )
        )
    else:
        console.print("[yellow]Keine SPEC.md im Repository gefunden.[/yellow]")


async def show_diff_preview(
    console: Console,
    git_handler: GitHandler,
    default_branch: str = "main",
) -> None:
    """Print the formatted git diff comparison against default branch."""
    try:
        diff_text = await git_handler.get_diff(target_branch=default_branch)
        if diff_text.strip():
            console.print(
                Panel(
                    diff_text[:4000] + ("\n... (gekürzt)" if len(diff_text) > 4000 else ""),
                    title="[bold yellow]Aktueller Git-Diff[/bold yellow]",
                    border_style="yellow",
                )
            )
        else:
            console.print("[dim]Keine Git-Änderungen zum Default-Branch vorhanden.[/dim]")
    except Exception as exc:
        console.print(f"[bold red]Fehler beim Abrufen des Git-Diffs:[/bold red] {exc}")


def print_cli_help_table(console: Console) -> None:
    """Print the formatted slash-command help cheat sheet."""
    table = Table(
        title="[bold cyan]Verfügbare Slash-Commands & Shortcuts[/bold cyan]",
        border_style="cyan",
        header_style="bold magenta",
    )
    table.add_column("Befehl", style="bold cyan", width=24)
    table.add_column("Parameter", style="dim", width=18)
    table.add_column("Beschreibung", style="white")

    table.add_row("<Anforderung>", "Prompt-Text", "Startet die autonome 5-Phasen Pipeline (Spec -> Dev -> CI -> Review -> Merge)")
    table.add_row("/agents", "-", "Zeigt alle registrierten Spezialagenten, Personas & Skills an")
    table.add_row("/status", "-", "Zeigt den aktuellen Task-Status und die Pipeline-Breadcrumbs")
    table.add_row("/skills", "-", "Zeigt alle aktiven Domain-Skills sortiert nach Pipeline-Phasen")
    table.add_row("/spec", "-", "Zeigt die generierte SPEC.md als gerendertes Markdown an")
    table.add_row("/diff", "-", "Zeigt den aktuellen Git-Diff zum Arbeits-Branch an")
    table.add_row("/config", "[show|key val]", "Interaktives Einstellungsmenü oder direkte Konfiguration")
    table.add_row("/provider", "[name]", "Zeigt oder wechselt den LLM-Provider (antigravity, claude, api, cli)")
    table.add_row("/resume", "[task_id]", "Setzt einen gespeicherten Workflow-Zustand fort")
    table.add_row("/clear", "-", "Leert den Bildschirm und zeichnet das Banner neu")
    table.add_row("/help, /?", "-", "Zeigt diese Hilfeübersicht an")
    table.add_row("/exit, /quit", "-", "Beendet die REPL-Session (auch via Ctrl+D)")

    console.print(table)


# =============================================================================
# AgentCLI REPL Engine
# =============================================================================


class AgentCLI:
    """Interactive modern REPL interface for the AI Orchestrator."""

    def __init__(
        self,
        config_path: str = "config.yaml",
        repo_path: str = ".",
        state_path: str = DEFAULT_STATE_FILE,
        console: Console | None = None,
        debug: bool = False,
    ) -> None:
        self.config_path = config_path
        self.repo_path = repo_path
        self.state_path = state_path
        self.console = console or Console()
        self.debug = debug

        self.config: AppConfig = load_config(config_path)
        self.skill_manager = SkillManager(config=self.config.skills)
        self.git_handler = GitHandler(repo_path=repo_path)

        # Autocompletion and history setup
        history_file = Path.home() / ".mergeflow_history"
        completer = NestedCompleter.from_nested_dict(CLI_SLASH_COMMANDS)
        try:
            self.session: PromptSession = PromptSession(
                history=FileHistory(str(history_file)),
                completer=completer,
                auto_suggest=AutoSuggestFromHistory(),
                style=PROMPT_STYLE,
                enable_history_search=True,
            )
        except Exception:
            # Fallback for headless environments, CI, or test runners without attached console
            from prompt_toolkit.input import DummyInput
            from prompt_toolkit.output import DummyOutput

            self.session = PromptSession(
                input=DummyInput(),
                output=DummyOutput(),
                history=FileHistory(str(history_file)),
                completer=completer,
                auto_suggest=AutoSuggestFromHistory(),
                style=PROMPT_STYLE,
                enable_history_search=True,
            )

    def print_help_table(self) -> None:
        """Render help cheat sheet to console."""
        print_cli_help_table(self.console)

    def print_agents_table(self) -> None:
        """Render registered agents overview table."""
        show_agents_table(self.console)

    async def handle_slash_command(self, user_input: str) -> bool:
        """Handle slash command. Returns True if handled, False if regular prompt."""
        cmd_stripped = user_input.strip()
        parts = cmd_stripped.split()
        root_cmd = parts[0].lower() if parts else ""

        if root_cmd in ("/exit", "/quit", "exit", "quit", ":q"):
            self.console.print("[dim]Auf Wiedersehen! 🚀[/dim]")
            raise SystemExit(0)

        elif root_cmd in ("/help", "/?", "help", "?"):
            self.print_help_table()
            return True

        elif root_cmd in ("/agents", "agents"):
            self.print_agents_table()
            return True

        elif root_cmd == "/clear":
            self.console.clear()
            print_banner(console=self.console, config=self.config, repo_path=self.repo_path)
            return True

        elif root_cmd in ("/skills", "skills"):
            show_skills_overview(self.skill_manager, self.console)
            return True

        elif root_cmd in ("/status", "status"):
            show_status_panel(self.console, state_path=self.state_path, config=self.config)
            return True

        elif root_cmd in ("/spec", "spec"):
            show_spec_preview(self.console, repo_path=self.repo_path)
            return True

        elif root_cmd in ("/diff", "diff"):
            await show_diff_preview(
                self.console,
                self.git_handler,
                default_branch=self.config.workflow.default_branch,
            )
            return True

        elif root_cmd in ("/provider", "provider"):
            if len(parts) == 1:
                self.console.print(
                    f"Aktiver LLM-Provider: [bold green]{self.config.providers.default_provider}[/bold green]"
                )
            else:
                new_provider = parts[1].lower()
                if new_provider in ("antigravity", "claude", "api", "cli"):
                    self.config.providers.default_provider = new_provider
                    self.console.print(f"[bold green]✓ Provider geändert auf: {new_provider}[/bold green]")
                else:
                    self.console.print(
                        f"[bold red]Ungültiger Provider '{new_provider}'. Erlaubt: antigravity, claude, api, cli[/bold red]"
                    )
            return True

        elif root_cmd in ("/config", "/settings", "config", "settings"):
            if len(parts) >= 3 and parts[1].lower() == "auto-merge":
                val = parts[2].lower() in ("true", "1", "on", "yes", "ja")
                self.config.workflow.auto_merge = val
                self.console.print(f"[bold green]✓ Auto-Merge auf {val} gesetzt.[/bold green]")
            elif len(parts) >= 3 and parts[1].lower() == "provider":
                self.config.providers.default_provider = parts[2].lower()
                self.console.print(f"[bold green]✓ Standard-Provider auf {parts[2]} gesetzt.[/bold green]")
            elif len(parts) == 1:
                interactive_settings_menu(config=self.config, console=self.console, config_path=self.config_path)
            else:
                show_settings_table(self.config, self.console)
            return True

        elif root_cmd.startswith("/resume"):
            req_task_id = parts[1] if len(parts) > 1 else None
            if not req_task_id:
                req_task_id = interactive_resume_menu(console=self.console, state_path=self.state_path)
                if not req_task_id:
                    return True

            await self.execute_orchestration(prompt="", task_id=req_task_id, resume_mode=True)
            return True

        return False

    async def execute_orchestration(
        self,
        prompt: str,
        task_id: str | None = None,
        resume_mode: bool = False,
    ) -> None:
        """Run orchestrator with real-time UI event integration."""
        emitter = CLIEventEmitter()
        ui_handler = TerminalUIEventHandler(console=self.console)
        emitter.subscribe(ui_handler.handle_event)

        ui_handler.start_spinner("[cyan]Starte Autonomous Multi-Agent Pipeline...[/cyan]")
        try:
            state = await run_orchestrator(
                requirements=prompt,
                config_path=self.config_path,
                repo_path=self.repo_path,
                state_path=self.state_path,
                console=self.console,
                auto_merge=self.config.workflow.auto_merge,
                provider_type=self.config.providers.default_provider,
                task_id=task_id,
                resume_from_state=resume_mode,
                event_emitter=emitter,
            )
            ui_handler.stop_spinner()

            breadcrumbs = format_phase_breadcrumbs(state.current_phase)
            commits_cnt = len(
                getattr(
                    state,
                    "commits_created",
                    state.metadata.get("commits", []) if isinstance(state.metadata, dict) else [],
                )
            )
            if state.current_phase == WorkflowPhase.DONE.value:
                self.console.print(
                    Panel(
                        f"[bold green]✓ Alle Phasen erfolgreich abgeschlossen![/bold green]\n\n"
                        f"Task-ID: [bold cyan]{state.task_id}[/bold cyan]\n"
                        f"Pipeline: {breadcrumbs}\n"
                        f"Branch: [bold yellow]{state.branch_name or 'main'}[/bold yellow]\n"
                        f"Commits: {commits_cnt}\n"
                        f"Persistiert in: [dim]{self.state_path}[/dim]",
                        title="[bold green]Workflow Erfolgreich[/bold green]",
                        border_style="green",
                    )
                )
            elif state.current_phase == WorkflowPhase.HUMAN_INTERVENTION_REQUIRED.value:
                self.console.print(
                    Panel(
                        f"[bold yellow]⏸ Workflow pausiert in Phase '{state.current_phase}'[/bold yellow]\n\n"
                        f"Pipeline: {breadcrumbs}\n"
                        f"Menschliches Eingreifen oder Fortsetzen erforderlich (/resume {state.task_id}).",
                        title="[bold yellow]Workflow Pausiert[/bold yellow]",
                        border_style="yellow",
                    )
                )
            else:
                self.console.print(
                    Panel(
                        f"[bold red]✗ Workflow endete in Phase: {state.current_phase}[/bold red]\n\nPipeline: {breadcrumbs}",
                        title="[bold red]Workflow Nicht Abgeschlossen[/bold red]",
                        border_style="red",
                    )
                )

        except DiscoveryCancelledError:
            ui_handler.stop_spinner()
            self.console.print("\n[yellow]Discovery-Phase wurde abgebrochen.[/yellow]")
        except KeyboardInterrupt:
            ui_handler.stop_spinner()
            self.console.print("\n[yellow]Workflow durch Benutzer unterbrochen (Ctrl+C).[/yellow]")
        except Exception as exc:
            ui_handler.stop_spinner()
            self.console.print(f"\n[bold red]Fehler während des Workflows:[/bold red] {exc}")

    async def run(self) -> int:
        """Run the main interactive REPL shell loop."""
        print_banner(console=self.console, config=self.config, repo_path=self.repo_path)
        self.console.print(
            "[dim]Gib eine Anforderung ein, um den Agent-Workflow zu starten, "
            "oder tippe [bold cyan]/agents[/bold cyan] oder [bold cyan]/help[/bold cyan].[/dim]\n"
        )

        prompt_html = HTML('<ansicyan><b>agent</b></ansicyan> <ansimagenta>❯</ansimagenta> ')

        while True:
            try:
                # Use asynchronous prompt_toolkit prompt
                user_input = await self.session.prompt_async(prompt_html)
                if not user_input or not user_input.strip():
                    continue

                user_input = user_input.strip()

                # Handle slash commands
                if user_input.startswith("/"):
                    handled = await self.handle_slash_command(user_input)
                    if handled:
                        continue

                # Strip optional /run prefix
                prompt_text = user_input
                if prompt_text.lower().startswith("/run "):
                    prompt_text = prompt_text[5:].strip()

                self.console.print(
                    f"\n[bold green]Starte Workflow für Anforderung:[/bold green] {prompt_text}\n"
                )
                await self.execute_orchestration(prompt=prompt_text)

            except KeyboardInterrupt:
                # User pressed Ctrl+C during typing - clear line and continue
                self.console.print(
                    "\n[dim yellow](Eingabe abgebrochen. Verwende /exit oder Ctrl+D zum Beenden)[/dim yellow]"
                )
                continue

            except EOFError:
                # User pressed Ctrl+D - exit gracefully
                self.console.print("\n[dim]Auf Wiedersehen! 🚀[/dim]")
                return 0

            except SystemExit as se:
                return int(se.code) if se.code is not None else 0

            except Exception as exc:
                self.console.print(f"[bold red]Unerwarteter REPL-Fehler:[/bold red] {exc}")
                if self.debug:
                    import traceback

                    traceback.print_exc()


async def run_cli(
    config_path: str = "config.yaml",
    repo_path: str = ".",
    state_path: str = DEFAULT_STATE_FILE,
    console: Console | None = None,
    debug: bool = False,
) -> int:
    """Convenience entry-point to instantiate and execute AgentCLI."""
    cli_app = AgentCLI(
        config_path=config_path,
        repo_path=repo_path,
        state_path=state_path,
        console=console,
        debug=debug,
    )
    return await cli_app.run()


async def run_interactive_session(
    config_path: str = "config.yaml",
    repo_path: str = ".",
    state_path: str = DEFAULT_STATE_FILE,
    console: Console | None = None,
    debug: bool = False,
) -> int:
    """Alias for run_cli for backward compatibility."""
    return await run_cli(
        config_path=config_path,
        repo_path=repo_path,
        state_path=state_path,
        console=console,
        debug=debug,
    )
