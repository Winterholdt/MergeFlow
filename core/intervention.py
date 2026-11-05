"""Intervention and cancellation management for MergeFlow orchestrator.

Enables graceful text-based user intervention when operations take long,
hang, or are interrupted via Ctrl+C, avoiding raw Python tracebacks.
"""

from __future__ import annotations

from enum import Enum
import logging
import sys
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

logger = logging.getLogger(__name__)


class InterventionAction(str, Enum):
    """User intervention actions when workflow is paused or interrupted."""

    SKIP = "skip"
    PAUSE = "pause"
    ABORT = "abort"
    CONTINUE = "continue"
    RETRY = "retry"


class OrchestrationCancelledError(Exception):
    """Raised when the workflow is explicitly cancelled/aborted by the user."""


class OrchestrationInterruptedError(Exception):
    """Raised when the workflow is interrupted or paused."""


def parse_intervention_command(command: str) -> InterventionAction:
    """Parse text input into an InterventionAction.

    Accepts German and English variations:
      - skip, s, weiter, überspringen, ueberspringen, weiterfahren, next -> SKIP
      - pause, p, pausieren, stop -> PAUSE
      - abort, a, cancel, abbrechen, exit, quit -> ABORT
      - continue, c, weiterlaufen, fortsetzen, run -> CONTINUE
      - retry, r, erneut, wiederholen -> RETRY
    """
    cmd = command.strip().lower()
    if cmd in ("s", "skip", "weiter", "überspringen", "ueberspringen", "weiterfahren", "next"):
        return InterventionAction.SKIP
    elif cmd in ("p", "pause", "pausieren"):
        return InterventionAction.PAUSE
    elif cmd in ("a", "abort", "cancel", "abbrechen", "exit", "quit", ":q"):
        return InterventionAction.ABORT
    elif cmd in ("c", "continue", "weiterlaufen", "fortsetzen", "run"):
        return InterventionAction.CONTINUE
    elif cmd in ("r", "retry", "erneut", "wiederholen"):
        return InterventionAction.RETRY
    return InterventionAction.ABORT


def prompt_user_intervention(
    console: Console | None = None,
    input_func: Callable[[str], str] | None = None,
    context: str = "Vorgang unterbrochen",
) -> InterventionAction:
    """Prompt the user for a text intervention action.

    Falls back gracefully to ABORT if non-interactive or second Ctrl+C occurs.
    """
    ui_console = console or Console()

    # Clear any active rich live/status spinners so terminal is clean and interactive
    if hasattr(ui_console, "_live_stack") and ui_console._live_stack:
        for live in list(ui_console._live_stack):
            try:
                live.stop()
            except Exception:
                pass
        ui_console._live_stack.clear()

    panel_text = (
        f"[bold yellow]⚠️  Eingriff erforderlich / Ausführung angehalten[/bold yellow]\n\n"
        f"[bold]Kontext:[/bold] {context}\n\n"
        f"Verfügbare Text-Befehle:\n"
        f"  [bold cyan][s] skip[/bold cyan]     - Aktuellen Schritt/Phase überspringen & fortfahren\n"
        f"  [bold cyan][p] pause[/bold cyan]    - Workflow pausieren & Zustand speichern (später via 'orch --resume')\n"
        f"  [bold cyan][a] abort[/bold cyan]    - Workflow sauber abbrechen (keine Python-Fehler)\n"
        f"  [bold cyan][c] continue[/bold cyan] - Ausführung fortsetzen"
    )
    ui_console.print(Panel(panel_text, title="[yellow]MergeFlow Interaktion[/yellow]", border_style="yellow"))

    try:
        if input_func:
            raw_input = input_func("Aktion wählen [s=skip, p=pause, a=abort, c=continue]: ")
        elif sys.stdin.isatty():
            raw_input = Prompt.ask(
                "[bold cyan]Aktion wählen[/bold cyan] ([bold green]s[/bold green]kip / [bold yellow]p[/bold yellow]ause / [bold red]a[/bold red]bort / [bold blue]c[/bold blue]ontinue)",
                console=ui_console,
                default="s",
            )
        else:
            logger.info("Non-interactive environment detected; defaulting to ABORT on intervention.")
            return InterventionAction.ABORT
    except (KeyboardInterrupt, EOFError):
        ui_console.print("\n[dim](Abbruch erkannt – beende Vorgang sauber)[/dim]")
        return InterventionAction.ABORT

    action = parse_intervention_command(raw_input)
    logger.info("User intervention selected: %s (from input: '%s')", action.value, raw_input)
    return action
