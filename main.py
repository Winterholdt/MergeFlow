"""CLI Entry-Point for MergeFlow Autonomous Multi-Agent Orchestrator.

Enables starting the orchestrator via:
  python main.py "Implement user authentication with JWT..."
or:
  python main.py --file requirements.txt

Loads configuration, initializes state, executes Phases 1 to 7 sequentially,
and persists workflow state after every phase.
"""

import argparse
import asyncio
import logging
from pathlib import Path
import sys

from rich.console import Console

from core.discovery import DiscoveryCancelledError
from core.models import WorkflowPhase
from core.orchestrator import run_orchestrator
from core.state_machine import determine_resume_phase, load_state

console = Console()


def build_parser() -> argparse.ArgumentParser:
    """Construct command-line argument parser.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="mergeflow",
        description="MergeFlow: Autonomous Multi-Agent Orchestrator (Phases 1-7)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py "Add an export to CSV endpoint in the reports module"
  python main.py --file requirements.txt
  python main.py --file requirements.txt --auto-merge
  python main.py "Fix bug in payment processing" --config custom_config.yaml
        """,
    )

    parser.add_argument(
        "requirements",
        nargs="?",
        default=None,
        help="Raw requirements description or task prompt.",
    )
    parser.add_argument(
        "-f",
        "--file",
        dest="file_path",
        type=str,
        default=None,
        help="Path to file containing requirements text.",
    )
    parser.add_argument(
        "-c",
        "--config",
        dest="config_path",
        type=str,
        default="config.yaml",
        help="Path to YAML configuration file (default: 'config.yaml').",
    )
    parser.add_argument(
        "-s",
        "--state",
        dest="state_path",
        type=str,
        default=".orchestrator/state.json",
        help="Path to JSON workflow state persistence file (default: '.orchestrator/state.json').",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        nargs="?",
        const="LAST",
        default=None,
        metavar="TASK_ID",
        help="Resume workflow from state persistence file. Optionally specify task_id.",
    )
    parser.add_argument(
        "-r",
        "--repo",
        dest="repo_path",
        type=str,
        default=".",
        help="Root path of target repository (default: '.').",
    )
    parser.add_argument(
        "-p",
        "--provider",
        dest="provider_type",
        type=str,
        choices=["cli", "api", "claude", "antigravity"],
        default=None,
        help="Override LLM provider profile.",
    )

    auto_merge_group = parser.add_mutually_exclusive_group()
    auto_merge_group.add_argument(
        "--auto-merge",
        dest="auto_merge",
        action="store_true",
        default=None,
        help="Automatically merge PR upon passing all reviews without interactive prompt.",
    )
    auto_merge_group.add_argument(
        "--no-auto-merge",
        dest="auto_merge",
        action="store_false",
        help="Require interactive user confirmation before merging PR.",
    )

    parser.add_argument(
        "--debug",
        dest="debug",
        action="store_true",
        help="Enable DEBUG logging output.",
    )

    return parser


async def async_main(args: argparse.Namespace) -> int:
    """Execute main asynchronous workflow.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for error).
    """
    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolve requirements input or resume state
    requirements_text: str | None = None
    target_state_path = Path(args.state_path).resolve()
    resume_mode = bool(args.resume)
    task_id_for_run = None

    if resume_mode:
        # Check primary state path or fallback to .orchestrator/state.json or state.json
        candidate_paths = [target_state_path, Path(".orchestrator/state.json").resolve(), Path("state.json").resolve()]
        existing_file = None
        for p in candidate_paths:
            if p.is_file():
                existing_file = p
                break

        if not existing_file:
            console.print(f"[bold red]Fehler:[/bold red] Kein Workflow-State zum Fortsetzen gefunden: {target_state_path}")
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
        requirements_text = args.requirements.strip()

    if not requirements_text and not resume_mode:
        console.print(
            "[bold red]Fehler:[/bold red] Keine Anforderung übergeben.\n"
            "Bitte Übergib die Anforderung direkt oder verwende --file <pfad> oder --resume [task_id].\n\n"
            "Verwendung:\n"
            '  python main.py "Hier ist die Anforderung..."\n'
            "  python main.py --file requirements.txt\n"
            "  python main.py --resume\n"
        )
        return 1

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
        )

        if state.current_phase == WorkflowPhase.DONE.value:
            console.print(
                f"\n[bold green]✓ Orchestrierung erfolgreich abgeschlossen! State gespeichert in: {target_state_path}[/bold green]"
            )
            return 0
        elif state.current_phase == WorkflowPhase.HUMAN_INTERVENTION_REQUIRED.value:
            console.print(
                f"\n[bold yellow]! Orchestrierung pausiert in Phase '{state.current_phase}'. "
                f"State gespeichert in: {target_state_path}[/bold yellow]"
            )
            return 0
        else:
            console.print(
                f"\n[bold red]✗ Orchestrierung endete in Phase '{state.current_phase}'. "
                f"State gespeichert in: {target_state_path}[/bold red]"
            )
            return 1

    except DiscoveryCancelledError:
        console.print("\n[yellow]Discovery-Phase wurde vom Benutzer abgebrochen.[/yellow]")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]Vorgang durch Benutzer unterbrochen (Ctrl+C).[/yellow]")
        return 130
    except Exception as exc:
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
