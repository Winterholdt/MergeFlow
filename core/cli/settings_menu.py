"""Interactive configuration, selection menus, and task management."""

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text
import yaml

from core.config import AppConfig
from core.models import WorkflowPhase, WorkflowState
from core.skills import DEFAULT_PHASE_SKILLS, SkillManager


def show_settings_table(config: AppConfig, console: Console) -> None:
    """Print current configuration settings in a structured Rich table."""
    table = Table(
        title="[bold cyan]MergeFlow Konfiguration[/bold cyan]",
        border_style="cyan",
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Option", style="bold white", no_wrap=True)
    table.add_column("Aktueller Wert", style="cyan")
    table.add_column("Beschreibung", style="dim")

    p_type = config.providers.default_provider
    table.add_row("Standard-Provider", f"[bold green]{p_type}[/bold green]", "Aktiver LLM Provider (antigravity, claude, api, cli)")

    if p_type == "api":
        table.add_row("API Model", config.providers.api.model, "Modellname für OpenAI / LiteLLM API")
        table.add_row("API Endpoint", config.providers.api.base_url, "Basis-URL der LLM API")
    elif p_type == "antigravity":
        table.add_row("Binary Path", config.providers.antigravity.binary_path, "Executable für Antigravity CLI")
    elif p_type == "claude":
        table.add_row("Binary Path", config.providers.claude.binary_path, "Executable für Claude Code CLI")
    else:
        table.add_row("Binary Path", config.providers.cli.binary_path, "Executable für generischen CLI-Provider")

    auto_merge_str = "[bold green]Aktiviert[/bold green]" if config.workflow.auto_merge else "[dim yellow]Deaktiviert[/dim yellow]"
    table.add_row("Auto-Merge", auto_merge_str, "Automatischer PR-Merge nach bestandenem Review")
    table.add_row("CI Max Retries", str(config.workflow.ci_max_retries), "Max. Reparaturversuche für fehlschlagende CI")
    table.add_row("Review Max Retries", str(config.workflow.review_max_retries), "Max. Nachbesserungsrunden für Code Reviews")
    table.add_row("Default Branch", config.workflow.default_branch, "Haupt-Branch für PR-Ziele und Diffs")
    table.add_row("Skills Aktiv", "[bold green]Ja[/bold green]" if config.skills.enabled else "[dim red]Nein[/dim red]", "Domain-Skills für Discovery, Dev & Reviews")

    console.print(table)


def save_config_to_yaml(config: AppConfig, path: Path | str = "config.yaml") -> None:
    """Save the updated configuration to a YAML file."""
    config_dict = {
        "environment": config.environment,
        "log_level": config.log_level,
        "providers": {
            "default_provider": config.providers.default_provider,
            "cli": {
                "binary_path": config.providers.cli.binary_path,
                "command_args": config.providers.cli.command_args,
                "timeout_seconds": config.providers.cli.timeout_seconds,
                "env": config.providers.cli.env,
            },
            "api": {
                "base_url": config.providers.api.base_url,
                "api_key": config.providers.api.api_key,
                "model": config.providers.api.model,
                "timeout_seconds": config.providers.api.timeout_seconds,
                "max_retries": config.providers.api.max_retries,
            },
            "claude": {
                "binary_path": config.providers.claude.binary_path,
                "command_args": config.providers.claude.command_args,
                "timeout_seconds": config.providers.claude.timeout_seconds,
                "env": config.providers.claude.env,
            },
            "antigravity": {
                "binary_path": config.providers.antigravity.binary_path,
                "command_args": config.providers.antigravity.command_args,
                "timeout_seconds": config.providers.antigravity.timeout_seconds,
                "env": config.providers.antigravity.env,
            },
        },
        "workflow": {
            "ci_max_retries": config.workflow.ci_max_retries,
            "review_max_retries": config.workflow.review_max_retries,
            "default_branch": config.workflow.default_branch,
            "working_directory": config.workflow.working_directory,
            "auto_merge": config.workflow.auto_merge,
        },
        "skills": {
            "enabled": config.skills.enabled,
            "directory": config.skills.directory,
            "phases": config.skills.phases,
        },
    }

    target = Path(path)
    with open(target, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_dict, f, default_flow_style=False, sort_keys=False)


def interactive_settings_menu(config: AppConfig, console: Console, config_path: str = "config.yaml") -> None:
    """Interactive settings and preferences configuration menu."""
    while True:
        console.print()
        show_settings_table(config, console)

        console.print(
            "\n[bold cyan]Was möchtest du ändern?[/bold cyan]\n"
            "  [bold green]1[/bold green]) LLM Provider wechseln (antigravity, claude, api, cli)\n"
            "  [bold green]2[/bold green]) Auto-Merge umschalten\n"
            "  [bold green]3[/bold green]) CI Max-Retries anpassen\n"
            "  [bold green]4[/bold green]) Review Max-Retries anpassen\n"
            "  [bold green]5[/bold green]) API-Modell ändern\n"
            "  [bold green]6[/bold green]) Einstellungen in config.yaml speichern\n"
            "  [bold yellow]0[/bold yellow]) Zurück zum Hauptmenü\n"
        )

        choice = Prompt.ask("[bold]Auswahl[/bold]", choices=["1", "2", "3", "4", "5", "6", "0"], default="0", console=console)

        if choice == "0":
            break
        elif choice == "1":
            new_p = Prompt.ask(
                "Wähle den Provider",
                choices=["antigravity", "claude", "api", "cli"],
                default=config.providers.default_provider,
                console=console,
            )
            config.providers.default_provider = new_p
            console.print(f"[bold green]✓ Provider geändert auf: {new_p}[/bold green]")
        elif choice == "2":
            new_auto = not config.workflow.auto_merge
            config.workflow.auto_merge = new_auto
            state_text = "aktiviert" if new_auto else "deaktiviert"
            console.print(f"[bold green]✓ Auto-Merge wurde {state_text}.[/bold green]")
        elif choice == "3":
            new_ci = IntPrompt.ask("Max. CI Retries (0-10)", default=config.workflow.ci_max_retries, console=console)
            config.workflow.ci_max_retries = max(0, min(10, new_ci))
            console.print(f"[bold green]✓ CI Max Retries gesetzt auf: {config.workflow.ci_max_retries}[/bold green]")
        elif choice == "4":
            new_rev = IntPrompt.ask("Max. Review Retries (0-10)", default=config.workflow.review_max_retries, console=console)
            config.workflow.review_max_retries = max(0, min(10, new_rev))
            console.print(f"[bold green]✓ Review Max Retries gesetzt auf: {config.workflow.review_max_retries}[/bold green]")
        elif choice == "5":
            new_model = Prompt.ask("API Modellname", default=config.providers.api.model, console=console).strip()
            if new_model:
                config.providers.api.model = new_model
                console.print(f"[bold green]✓ API Modell gesetzt auf: {new_model}[/bold green]")
        elif choice == "6":
            try:
                save_config_to_yaml(config, config_path)
                console.print(f"[bold green]✓ Einstellungen erfolgreich gespeichert in: {config_path}[/bold green]")
            except Exception as exc:
                console.print(f"[bold red]Fehler beim Speichern der Konfiguration:[/bold red] {exc}")


def interactive_resume_menu(console: Console, state_path: Path | str = ".orchestrator/state.json") -> str | None:
    """Interactively display available tasks to resume and return selected task_id."""
    state_file = Path(state_path)
    if not state_file.is_file():
        # Check fallback
        state_file = Path("state.json")
        if not state_file.is_file():
            console.print("[bold yellow]Keine gespeicherten Tasks gefunden.[/bold yellow]")
            return None

    try:
        state = WorkflowState.load_from_file(state_file)
        table = Table(
            title="[bold cyan]Gefundener Workflow-Status[/bold cyan]",
            border_style="cyan",
            header_style="bold magenta",
        )
        table.add_column("Task ID", style="bold cyan")
        table.add_column("Phase", style="yellow")
        table.add_column("Branch", style="green")
        table.add_column("Anforderung", style="white")

        req_preview = (state.raw_requirements[:60] + "...") if len(state.raw_requirements) > 60 else state.raw_requirements
        table.add_row(state.task_id, state.current_phase, state.branch_name or "-", req_preview)
        console.print(table)

        if Confirm.ask(f"\nMöchtest du Task '[bold cyan]{state.task_id}[/bold cyan]' fortsetzen?", default=True, console=console):
            return state.task_id
        return None
    except Exception as exc:
        console.print(f"[bold red]Fehler beim Lesen des State-Files:[/bold red] {exc}")
        return None


def show_skills_overview(skill_manager: SkillManager, console: Console) -> None:
    """Display available domain skills categorized by pipeline phase."""
    table = Table(
        title="[bold cyan]Verfügbare MergeFlow Domain-Skills[/bold cyan]",
        border_style="cyan",
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Phase / Kategorie", style="bold cyan")
    table.add_column("Skill Datei", style="green")
    table.add_column("Status", style="white")

    categories = [
        ("Phase 1: Discovery", "discovery"),
        ("Phase 2: Dev Loop", "dev"),
        ("Phase 4: Review (Security)", "review_security"),
        ("Phase 4: Review (Architecture)", "review_architecture"),
        ("Phase 4: Review (QA/Tests)", "review_qa"),
    ]

    phase_mapping = skill_manager.phases_config or DEFAULT_PHASE_SKILLS
    for label, cat_key in categories:
        skill_files = phase_mapping.get(cat_key, [])
        if not skill_files:
            table.add_row(label, "[dim]Keine konfiguriert[/dim]", "[dim]N/A[/dim]")
        else:
            for s_file in skill_files:
                table.add_row(label, s_file, "[bold green]Geladen[/bold green]")

    console.print(table)
