"""CLI Entry-Point for MergeFlow Autonomous Multi-Agent Orchestrator.

Enables starting the orchestrator via:
  orch                                      (starts modern interactive Agent-REPL)
  orch "Implement user authentication..."   (direct pipeline execution)
  orch agents                               (displays registered agents & personas)
  orch status                               (displays workflow state & breadcrumbs)
  orch spec                                 (displays SPEC.md preview)
  orch diff                                 (displays git diff against default branch)
  orch skills                               (displays domain skills overview)
  orch config                               (interactive settings configuration)
  orch --file requirements.txt              (reads requirements from file)
  orch --resume [task_id]                   (resumes existing state)
"""

from pathlib import Path
import sys

# Ensure project root is in sys.path when invoked via entrypoint script
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

import argparse
import asyncio
import logging

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from core.cli.events import CLIEventEmitter
from core.cli.formatting import format_phase_breadcrumbs
from core.cli.logger import setup_cli_logging
from core.cli.repl import (
    AgentCLI,
    run_cli,
    run_interactive_session,
    show_agents_table,
    show_diff_preview,
    show_spec_preview,
    show_status_panel,
)
from core.cli.settings_menu import (
    interactive_settings_menu,
    show_settings_table,
    show_skills_overview,
)
from core.cli.ui_handler import TerminalUIEventHandler
from core.config import AppConfig, load_config
from core.discovery import DiscoveryCancelledError
from core.models import WorkflowPhase
from core.orchestrator import run_orchestrator
from core.skills import SkillManager
from core.state_machine import determine_resume_phase, load_state
from vcs.git_handler import GitHandler

console = Console()

SUBCOMMANDS = {
    "agents",
    "status",
    "spec",
    "diff",
    "skills",
    "config",
    "settings",
    "interactive",
    "repl",
    "run",
}


def build_parser() -> argparse.ArgumentParser:
    """Construct command-line argument parser.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="orch",
        description="MergeFlow: Autonomous Multi-Agent Orchestrator (Phases 1-5)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Verwendung & Befehle:
  orch                                   Startet die moderne interaktive Agent-Shell (REPL)
  orch "Anforderung..."                  Führt die Anforderung direkt im Terminal aus
  orch agents                            Zeigt alle registrierten Spezialagenten & Personas
  orch status                            Zeigt den aktuellen Workflow-Status & Pipeline-Phase
  orch spec                              Zeigt die generierte SPEC.md Vorschau
  orch diff                              Zeigt den aktuellen Git-Diff zum Default-Branch
  orch skills                            Zeigt alle aktiven Domain-Skills
  orch config                            Öffnet das interaktive Konfigurationsmenü
  orch -f requirements.txt               Liest Anforderungen aus Datei
  orch --resume [task_id]                Setzt vorherigen Task fort
        """,
    )

    parser.add_argument(
        "requirements",
        nargs="?",
        default=None,
        help="Subcommand (agents, status, spec, diff, skills, config) oder Task-Prompt.",
    )
    parser.add_argument(
        "extra_words",
        nargs="*",
        default=[],
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-f",
        "--file",
        dest="file_path",
        type=str,
        default=None,
        help="Pfad zu einer Datei mit Anforderungs-Text.",
    )
    parser.add_argument(
        "-c",
        "--config",
        dest="config_path",
        type=str,
        default="config.yaml",
        help="Pfad zur YAML-Konfigurationsdatei (Standard: 'config.yaml').",
    )
    parser.add_argument(
        "-s",
        "--state",
        dest="state_path",
        type=str,
        default=".orchestrator/state.json",
        help="Pfad zur Workflow-State JSON-Datei (Standard: '.orchestrator/state.json').",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        nargs="?",
        const="LAST",
        default=None,
        metavar="TASK_ID",
        help="Setzt Workflow fort. Optional task_id angeben.",
    )
    parser.add_argument(
        "-r",
        "--repo",
        dest="repo_path",
        type=str,
        default=".",
        help="Wurzelverzeichnis des Ziel-Repositories (Standard: '.').",
    )
    parser.add_argument(
        "-p",
        "--provider",
        dest="provider_type",
        type=str,
        choices=["cli", "api", "claude", "antigravity"],
        default=None,
        help="Überschreibt das LLM-Provider-Profil.",
    )

    auto_merge_group = parser.add_mutually_exclusive_group()
    auto_merge_group.add_argument(
        "--auto-merge",
        dest="auto_merge",
        action="store_true",
        default=None,
        help="Führt PR-Merge automatisch nach bestandenen Reviews aus.",
    )
    auto_merge_group.add_argument(
        "--no-auto-merge",
        dest="auto_merge",
        action="store_false",
        help="Erfordert Benutzerbestätigung vor dem Mergen des PRs.",
    )

    parser.add_argument(
        "-i",
        "--interactive",
        dest="interactive",
        action="store_true",
        help="Startet die interaktive Agent-Shell mit Slash-Commands.",
    )
    parser.add_argument(
        "--debug",
        dest="debug",
        action="store_true",
        help="Aktiviert ausführliches DEBUG-Logging.",
    )

    return parser


async def handle_subcommand(cmd: str, args: argparse.Namespace) -> int | None:
    """Check if first positional argument is a CLI subcommand and execute it.

    Returns:
        Exit code if handled, None if not a subcommand (regular requirement).
    """
    cmd_lower = cmd.lower()

    if cmd_lower in ("agents", "/agents"):
        show_agents_table(console)
        return 0

    elif cmd_lower in ("status", "/status"):
        cfg = load_config(args.config_path) if Path(args.config_path).is_file() else None
        show_status_panel(console, state_path=args.state_path, config=cfg)
        return 0

    elif cmd_lower in ("spec", "/spec"):
        show_spec_preview(console, repo_path=args.repo_path)
        return 0

    elif cmd_lower in ("diff", "/diff"):
        cfg = load_config(args.config_path) if Path(args.config_path).is_file() else None
        def_branch = cfg.workflow.default_branch if cfg else "main"
        await show_diff_preview(
            console,
            GitHandler(repo_path=args.repo_path),
            default_branch=def_branch,
        )
        return 0

    elif cmd_lower in ("skills", "/skills"):
        cfg = load_config(args.config_path)
        show_skills_overview(SkillManager(config=cfg.skills), console)
        return 0

    elif cmd_lower in ("config", "/config", "settings", "/settings"):
        cfg = load_config(args.config_path)
        interactive_settings_menu(config=cfg, console=console, config_path=args.config_path)
        return 0

    elif cmd_lower in ("interactive", "repl", "-i"):
        return await run_interactive_session(
            config_path=args.config_path,
            repo_path=args.repo_path,
            state_path=args.state_path,
            console=console,
            debug=args.debug,
        )

    return None


async def async_main(args: argparse.Namespace) -> int:
    """Execute main asynchronous workflow.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for error).
    """
    # Check if explicit interactive REPL requested
    if getattr(args, "interactive", False):
        return await run_interactive_session(
            config_path=args.config_path,
            repo_path=args.repo_path,
            state_path=args.state_path,
            console=console,
            debug=args.debug,
        )

    # Check for direct subcommands (orch agents, orch status, etc.)
    if args.requirements:
        all_words = [args.requirements] + (getattr(args, "extra_words", None) or [])
        first_word = all_words[0]
        if first_word.lower() == "run" and len(all_words) > 1:
            # Strip leading 'run'
            all_words = all_words[1:]
        elif len(all_words) == 1:
            subcmd_res = await handle_subcommand(first_word, args)
            if subcmd_res is not None:
                return subcmd_res

    # Configure logging
    setup_cli_logging(console=console, debug=args.debug)

    # Resolve requirements input or resume state
    requirements_text: str | None = None
    target_state_path = Path(args.state_path).resolve()
    resume_mode = bool(args.resume)
    task_id_for_run = None

    if resume_mode:
        candidate_paths = [
            target_state_path,
            Path(".orchestrator/state.json").resolve(),
            Path("state.json").resolve(),
        ]
        existing_file = None
        for p in candidate_paths:
            if p.is_file():
                existing_file = p
                break

        if not existing_file:
            console.print(
                f"[bold red]Fehler:[/bold red] Kein Workflow-State zum Fortsetzen gefunden: {target_state_path}"
            )
            return 1

        try:
            expected_tid = args.resume if args.resume != "LAST" else None
            restored_state = load_state(existing_file, task_id=expected_tid)
            requirements_text = restored_state.raw_requirements
            task_id_for_run = restored_state.task_id
            target_state_path = existing_file
            resume_phase = determine_resume_phase(restored_state)
            console.print(
                f"[bold cyan]Setze Workflow für Task '{restored_state.task_id}' in Phase '{resume_phase.value}' fort...[/bold cyan]"
            )
        except Exception as exc:
            console.print(f"[bold red]Fehler beim Laden des State für Resume:[/bold red] {exc}")
            return 1

    elif args.file_path:
        req_file = Path(args.file_path).resolve()
        if not req_file.is_file():
            console.print(f"[bold red]Fehler:[/bold red] Anforderungsdatei nicht gefunden: {req_file}")
            return 1
        try:
            requirements_text = req_file.read_text(encoding="utf-8").strip()
        except Exception as exc:
            console.print(f"[bold red]Fehler beim Lesen der Datei '{req_file}':[/bold red] {exc}")
            return 1

    elif args.requirements:
        all_words = [args.requirements] + (getattr(args, "extra_words", None) or [])
        if all_words[0].lower() == "run" and len(all_words) > 1:
            all_words = all_words[1:]
        requirements_text = " ".join(all_words).strip()

    # If no requirements and no resume:
    if not requirements_text and not resume_mode:
        # In a real terminal (TTY), launch modern Agent REPL shell directly!
        if sys.stdin.isatty():
            return await run_interactive_session(
                config_path=args.config_path,
                repo_path=args.repo_path,
                state_path=args.state_path,
                console=console,
                debug=args.debug,
            )

        # In non-interactive pipe (e.g. cat req.txt | orch)
        try:
            stdin_content = sys.stdin.read().strip()
            if stdin_content:
                requirements_text = stdin_content
        except Exception:
            pass

        if not requirements_text:
            console.print(
                "[bold red]Fehler:[/bold red] Keine Anforderung übergeben.\n"
                "Verwendung: orch \"Anforderung...\" oder einfach 'orch' für die interaktive Shell."
            )
            return 1

    # Execute workflow with UI event handler and status spinner
    emitter = CLIEventEmitter()
    ui_handler = TerminalUIEventHandler(console=console)
    emitter.subscribe(ui_handler.handle_event)

    def cli_input_func(prompt_label: str) -> str:
        ui_handler.stop_spinner()
        if hasattr(console, "_live_stack") and console._live_stack:
            for live in list(console._live_stack):
                try:
                    live.stop()
                except Exception:
                    pass
            console._live_stack.clear()
        return Prompt.ask(prompt_label, console=console)

    ui_handler.start_spinner("[cyan]Starte Autonomous Multi-Agent Pipeline...[/cyan]")

    try:
        state = await run_orchestrator(
            requirements=requirements_text or "",
            config_path=args.config_path,
            repo_path=args.repo_path,
            state_path=target_state_path,
            auto_merge=args.auto_merge,
            provider_type=args.provider_type,
            console=console,
            task_id=task_id_for_run,
            resume_from_state=resume_mode,
            event_emitter=emitter,
            input_func=cli_input_func,
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
            console.print(
                Panel(
                    f"[bold green]✓ Orchestrierung erfolgreich abgeschlossen![/bold green]\n\n"
                    f"Task-ID: [bold cyan]{state.task_id}[/bold cyan]\n"
                    f"Pipeline: {breadcrumbs}\n"
                    f"Branch: [bold yellow]{state.branch_name or 'main'}[/bold yellow]\n"
                    f"Commits: {commits_cnt}\n"
                    f"Persistiert in: [dim]{target_state_path}[/dim]",
                    title="[bold green]Workflow Erfolgreich[/bold green]",
                    border_style="green",
                )
            )
            return 0
        elif state.current_phase == WorkflowPhase.HUMAN_INTERVENTION_REQUIRED.value:
            console.print(
                Panel(
                    f"[bold yellow]⏸ Workflow pausiert in Phase '{state.current_phase}'[/bold yellow]\n\n"
                    f"Pipeline: {breadcrumbs}\n"
                    f"Menschliches Eingreifen erforderlich. Fortsetzen via: [bold cyan]orch --resume {state.task_id}[/bold cyan]",
                    title="[bold yellow]Workflow Pausiert[/bold yellow]",
                    border_style="yellow",
                )
            )
            return 0
        else:
            console.print(
                Panel(
                    f"[bold red]✗ Orchestrierung endete in Phase: {state.current_phase}[/bold red]\n\n"
                    f"Pipeline: {breadcrumbs}",
                    title="[bold red]Workflow Nicht Abgeschlossen[/bold red]",
                    border_style="red",
                )
            )
            return 1

    except DiscoveryCancelledError:
        ui_handler.stop_spinner()
        console.print("\n[yellow]Discovery-Phase wurde vom Benutzer abgebrochen.[/yellow]")
        return 1
    except KeyboardInterrupt:
        ui_handler.stop_spinner()
        console.print("\n[yellow]Vorgang durch Benutzer unterbrochen (Ctrl+C).[/yellow]")
        return 130
    except Exception as exc:
        ui_handler.stop_spinner()
        console.print(f"\n[bold red]Unerwarteter Fehler während der Orchestrierung:[/bold red] {exc}")
        logging.exception("Fatal orchestration failure:")
        return 1


def main() -> None:
    """CLI entry-point."""
    parser = build_parser()
    args = parser.parse_args()
    exit_code = asyncio.run(async_main(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
