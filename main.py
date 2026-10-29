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
        default="state.json",
        help="Path to JSON workflow state persistence file (default: 'state.json').",
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

    # Resolve requirements input
    requirements_text: str | None = None

    if args.file_path:
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

    if not requirements_text:
        console.print(
            "[bold red]Fehler:[/bold red] Keine Anforderung übergeben.\n"
            "Bitte Übergib die Anforderung direkt oder verwende --file <pfad>.\n\n"
            "Verwendung:\n"
            '  python main.py "Hier ist die Anforderung..."\n'
            "  python main.py --file requirements.txt\n"
        )
        return 1

    try:
        state = await run_orchestrator(
            requirements=requirements_text,
            config_path=args.config_path,
            repo_path=args.repo_path,
            state_path=args.state_path,
            auto_merge=args.auto_merge,
            provider_type=args.provider_type,
            console=console,
        )

        if state.current_phase == WorkflowPhase.DONE.value:
            console.print(
                f"\n[bold green]✓ Orchestrierung erfolgreich abgeschlossen! State gespeichert in: {args.state_path}[/bold green]"
            )
            return 0
        else:
            console.print(
                f"\n[bold red]✗ Orchestrierung endete in Phase '{state.current_phase}'. "
                f"State gespeichert in: {args.state_path}[/bold red]"
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
