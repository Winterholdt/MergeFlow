"""Advanced text input engine using prompt_toolkit for MergeFlow CLI."""

from pathlib import Path
import sys
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter, WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console

SLASH_COMMANDS = {
    "/help": None,
    "/?": None,
    "/config": {
        "show": None,
        "provider": {"cli": None, "api": None, "claude": None, "antigravity": None},
        "auto-merge": {"on": None, "off": None},
        "ci-retries": None,
        "review-retries": None,
    },
    "/provider": {
        "cli": None,
        "api": None,
        "claude": None,
        "antigravity": None,
    },
    "/skills": None,
    "/status": None,
    "/spec": None,
    "/history": None,
    "/resume": None,
    "/diff": None,
    "/multiline": None,
    "/clear": None,
    "/exit": None,
    "/quit": None,
}

PROMPT_STYLE = Style.from_dict({
    "prompt-brand": "#00d7ff bold",
    "prompt-arrow": "#af00ff bold",
    "prompt-mode": "#888888 italic",
})


class CLIPrompter:
    """Manages interactive terminal input with history and autocompletion."""

    def __init__(self, history_dir: Path | str = ".orchestrator") -> None:
        self.history_dir = Path(history_dir)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.history_dir / ".history"

        self.completer = NestedCompleter.from_nested_dict(SLASH_COMMANDS)
        self.multiline_mode = False

        try:
            self.session: PromptSession[str] = PromptSession(
                history=FileHistory(str(self.history_file)),
                completer=self.completer,
                style=PROMPT_STYLE,
                enable_history_search=True,
            )
        except Exception:
            self.session = None  # type: ignore

    def get_input(self, console: Console, prompt_label: str = "mergeflow") -> str:
        """Prompt user for input with history and completion, or fallback to console.

        Args:
            console: Rich Console instance.
            prompt_label: Prefix label for prompt.

        Returns:
            Entered string stripped of leading/trailing whitespace.
        """
        # If not a tty or prompt_toolkit session unavailable, fallback
        if not sys.stdin.isatty() or self.session is None:
            try:
                return console.input(f"[bold cyan]{prompt_label}[/bold cyan] [bold magenta]❯[/bold magenta] ").strip()
            except (EOFError, KeyboardInterrupt):
                return "/exit"

        mode_indicator = " (multiline: ESC+Enter to submit)" if self.multiline_mode else ""
        prompt_fragments = [
            ("class:prompt-brand", prompt_label),
            ("class:prompt-mode", mode_indicator),
            ("class:prompt-arrow", " ❯ "),
        ]

        try:
            text = self.session.prompt(
                prompt_fragments,
                multiline=self.multiline_mode,
            )
            return text.strip()
        except KeyboardInterrupt:
            # User hit Ctrl+C - return empty to let REPL continue
            console.print("\n[dim](Abgebrochen - Eingabe geleert. Tippe /exit zum Beenden.)[/dim]")
            return ""
        except EOFError:
            # User hit Ctrl+D - exit
            return "/exit"
