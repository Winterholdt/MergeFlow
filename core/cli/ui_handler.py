"""Terminal UI and Rich event handler for MergeFlow CLI."""

from __future__ import annotations

import logging
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.status import Status

from core.cli.events import CLIEvent, EventType

logger = logging.getLogger(__name__)


class TerminalUIEventHandler:
    """Listens to CLI events and updates the Rich console and status spinner."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._status: Status | None = None
        self._active_agent: str | None = None
        self._accumulated_tokens: list[str] = []

    def start_spinner(self, initial_text: str = "[cyan]Initialisiere Orchestrator...[/cyan]") -> None:
        """Start the interactive status spinner."""
        if self._status is None:
            self._status = self.console.status(initial_text, spinner="dots")
            self._status.start()
        else:
            self._status.update(initial_text)

    def stop_spinner(self) -> None:
        """Stop and clear the status spinner."""
        if self._status is not None:
            self._status.stop()
            self._status = None

    def handle_event(self, event: CLIEvent) -> None:
        """Process incoming lifecycle event and update terminal UI."""
        event_type = event.event_type
        if isinstance(event_type, EventType):
            etype = event_type.value
        else:
            etype = str(event_type)

        if etype == EventType.ROUTING.value:
            msg = event.message or "Analysiere Eingabe & wähle Agenten aus..."
            if self._status:
                self._status.update(f"[bold cyan]⚡ {msg}[/bold cyan]")
            else:
                self.console.print(f"[dim cyan]⚡ {msg}[/dim cyan]")

        elif etype == EventType.AGENT_START.value:
            self._active_agent = event.agent_name
            role = f" ({event.role})" if event.role else ""
            msg = event.message or "übernimmt die Ausführung..."
            text = f"[bold magenta]🤖 [{event.agent_name}]{role}[/bold magenta] [dim]{msg}[/dim]"
            if self._status:
                self._status.update(text)
            else:
                self.console.print(text)

        elif etype == EventType.AGENT_END.value:
            self.stop_spinner()
            agent = event.agent_name or self._active_agent or "Agent"
            msg = event.message or "Arbeit abgeschlossen."
            self.console.print(f"[dim green]✓ [{agent}] {msg}[/dim green]")

        elif etype == EventType.TOOL_START.value:
            tool = event.tool_name or "Tool"
            msg = event.message or f"Führe {tool} aus..."
            agent_prefix = f"[{event.agent_name}] " if event.agent_name else ""
            if self._status:
                self._status.update(f"[dim yellow]🔧 {agent_prefix}{msg}[/dim yellow]")
            else:
                self.console.print(f"[dim yellow]🔧 {agent_prefix}{msg}[/dim yellow]")

        elif etype == EventType.TOOL_END.value:
            tool = event.tool_name or "Tool"
            msg = event.message or "abgeschlossen."
            if self._status:
                self._status.update(f"[dim green]✓ Tool {tool}: {msg}[/dim green]")

        elif etype == EventType.TOKEN.value:
            if event.token:
                self._accumulated_tokens.append(event.token)

        elif etype == EventType.STATUS.value:
            msg = event.message or ""
            if self._status:
                self._status.update(f"[cyan]ℹ {msg}[/cyan]")
            else:
                self.console.print(f"[cyan]ℹ {msg}[/cyan]")

        elif etype in (EventType.USER_PROMPT_START.value, "user_prompt_start"):
            self.stop_spinner()

        elif etype == EventType.DONE.value:
            self.stop_spinner()
            if event.content:
                self.console.print(Markdown(event.content))
            elif event.message:
                self.console.print(f"[bold green]✓ {event.message}[/bold green]")

        elif etype == EventType.ERROR.value:
            self.stop_spinner()
            msg = event.message or (event.data.get("error") if event.data else "Unbekannter Fehler")
            self.console.print(f"[bold red]✗ {msg}[/bold red]")
