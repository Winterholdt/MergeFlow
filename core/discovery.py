"""Interactive Requirements Discovery & Specification Loop for MergeFlow.

This module coordinates Phase 3 of the orchestration pipeline:
1. Gathers repository context (tech stack, dependencies, README).
2. Sends the discovery prompt to the LLM provider to uncover blind spots and formulate clarifying questions.
3. Interactively presents questions to the user in the terminal via Rich and collects answers.
4. Generates an authoritative SPEC.md incorporating requirements, context, and user answers.
5. Provides an interactive approval dialog ([A]pprove & Start Dev, [E]dit, [C]ancel).
"""

from collections.abc import Callable
import json
import logging
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, Field
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from core.provider import BaseLLMProvider

logger = logging.getLogger(__name__)


class DiscoveryError(Exception):
    """Base exception for discovery loop errors."""


class DiscoveryCancelledError(DiscoveryError):
    """Raised when the user explicitly cancels the discovery and spec generation."""


class DiscoveryAnalysis(BaseModel):
    """Structured response from the LLM discovery agent."""

    summary: str = Field(
        ...,
        description="Concise summary of the user's intent and technical goals.",
    )
    blind_spots: list[str] = Field(
        default_factory=list,
        description="Key architectural risks, unstated assumptions, or edge cases.",
    )
    questions: list[str] = Field(
        default_factory=list,
        description="Numbered clarifying questions requiring user resolution before coding.",
    )


def gather_repo_context(repo_path: Path, max_file_chars: int = 3500) -> str:
    """Scan and summarize the repository tech stack, structure, and configuration.

    Args:
        repo_path: Path to the target repository root.
        max_file_chars: Maximum characters to read per configuration file.

    Returns:
        Formatted Markdown string representing repository context.
    """
    repo = Path(repo_path).resolve()
    if not repo.exists() or not repo.is_dir():
        return f"Repository path '{repo_path}' is not an accessible directory."

    sections: list[str] = []

    # 1. Directory Structure overview (top-level)
    try:
        ignored = {
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            "node_modules",
            ".pytest_cache",
            ".idea",
            ".vscode",
            ".tox",
            "dist",
            "build",
        }
        entries = sorted(
            [e.name + ("/" if e.is_dir() else "") for e in repo.iterdir() if e.name not in ignored]
        )
        sections.append("#### Top-Level Repository Structure:\n" + ", ".join(entries))
    except Exception as exc:
        logger.warning("Could not list repository directory: %s", exc)

    # 2. Key configuration and doc files
    candidate_files = [
        "pyproject.toml",
        "package.json",
        "requirements.txt",
        "Cargo.toml",
        "go.mod",
        "README.md",
        "README.rst",
        "config.yaml",
    ]

    found_files: list[str] = []
    for rel_name in candidate_files:
        target = repo / rel_name
        if target.is_file():
            try:
                content = target.read_text(encoding="utf-8", errors="replace").strip()
                if len(content) > max_file_chars:
                    content = content[:max_file_chars] + f"\n... [truncated at {max_file_chars} chars]"
                found_files.append(f"##### `{rel_name}`:\n```\n{content}\n```")
            except Exception as read_err:
                logger.warning("Could not read '%s': %s", target, read_err)

    if found_files:
        sections.append("#### Detected Project Manifests & Context:\n" + "\n\n".join(found_files))
    else:
        sections.append("#### Detected Project Manifests & Context:\nNo standard manifests found.")

    return "\n\n".join(sections)


def load_discovery_prompt(prompt_path: Path | None = None) -> str:
    """Load the system prompt for the discovery agent from file or fallback default.

    Args:
        prompt_path: Optional explicit path to the markdown prompt file.

    Returns:
        System prompt string.
    """
    if prompt_path is None:
        prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "spec_discovery.md"

    if prompt_path.is_file():
        try:
            return prompt_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not read prompt from '%s': %s. Using default.", prompt_path, exc)

    # Fallback system prompt if file is missing
    return (
        "You are the Senior Software Architect and Discovery Agent of MergeFlow. "
        "Your mission is to evaluate requirements against repository context, identify blind spots, "
        "ask essential clarifying questions, and generate a battle-tested SPEC.md."
    )


def _clean_markdown_fence(text: str) -> str:
    """Strip outermost ```markdown ... ``` wrapper if the entire response is enclosed."""
    stripped = text.strip()
    match = re.fullmatch(r"^```(?:markdown)?\s*\n([\s\S]*?)\n```$", stripped, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return stripped


def _parse_discovery_analysis(response: Any) -> DiscoveryAnalysis:
    """Normalize provider output into a validated DiscoveryAnalysis model."""
    if isinstance(response, DiscoveryAnalysis):
        return response

    if isinstance(response, dict):
        return DiscoveryAnalysis.model_validate(response)

    if isinstance(response, str):
        # Try direct JSON parsing
        try:
            data = json.loads(response)
            if isinstance(data, dict):
                return DiscoveryAnalysis.model_validate(data)
        except Exception:
            pass

        # Check for JSON code fence
        json_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", response, re.IGNORECASE)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                return DiscoveryAnalysis.model_validate(data)
            except Exception:
                pass

        # Heuristic fallback parsing if plain text was returned
        lines = [line.strip() for line in response.splitlines() if line.strip()]
        questions: list[str] = []
        blind_spots: list[str] = []
        summary_lines: list[str] = []

        for line in lines:
            if re.match(r"^(?:Q\d+[:.]|\d+[\.)]|-\s*\[?\s*\?\]?)\s+", line):
                questions.append(re.sub(r"^(?:Q\d+[:.]|\d+[\.)]|-\s*\[?\s*\?\]?)\s+", "", line))
            elif line.startswith("-") or line.startswith("*"):
                blind_spots.append(line.lstrip("-* ").strip())
            else:
                summary_lines.append(line)

        summary_text = " ".join(summary_lines) or "Requirements analysis completed."
        return DiscoveryAnalysis(
            summary=summary_text,
            blind_spots=blind_spots,
            questions=questions,
        )

    raise DiscoveryError(f"Unexpected provider response format: {type(response)}")


async def run_discovery_phase(
    raw_requirements: str,
    provider: BaseLLMProvider,
    repo_path: Path,
    console: Console | None = None,
    input_func: Callable[[str], str] | None = None,
) -> str:
    """Execute the interactive discovery & specification loop.

    Steps:
    1. Reads repository context (README, pyproject.toml, package.json, etc.).
    2. Sends discovery prompt to LLM to uncover blind spots and generate questions.
    3. Displays questions via Rich and prompts user for answers interactively.
    4. Sends answers back to LLM to formulate the final SPEC.md with acceptance criteria.
    5. Displays approval dialog: [A]pprove & Start Dev / [E]dit / [C]ancel.
       - 'A': Writes SPEC.md to repo_path and returns the spec content.
       - 'E': Prompts user for feedback, regenerates SPEC.md, and repeats approval.
       - 'C': Cancels cleanly by raising DiscoveryCancelledError.

    Args:
        raw_requirements: The initial unrefined user or system requirements.
        provider: Initialized LLM provider implementing BaseLLMProvider.
        repo_path: Root path of the repository being modified.
        console: Optional Rich Console instance for output rendering.
        input_func: Optional input function for prompting (defaults to Prompt.ask).

    Returns:
        The approved SPEC.md markdown string.

    Raises:
        DiscoveryCancelledError: If the user cancels via [C].
        DiscoveryError: If LLM communication or file operations fail.
    """
    ui_console = console or Console()
    repo_path = Path(repo_path)

    if not raw_requirements.strip():
        raise DiscoveryError("Raw requirements cannot be empty.")

    ui_console.print(
        Panel(
            "[bold cyan]Phase 3: Requirements Discovery & Spec Generation[/bold cyan]\n"
            "Analyzing repository context, detecting blind spots, and preparing specification...",
            title="[bold blue]MergeFlow Orchestrator[/bold blue]",
            border_style="blue",
        )
    )

    # -------------------------------------------------------------------------
    # Schritt 1: Repo-Kontext einlesen
    # -------------------------------------------------------------------------
    with ui_console.status("[cyan]Reading repository context and manifests...", spinner="dots"):
        repo_context = gather_repo_context(repo_path)
    ui_console.print("[green]✓[/green] Repository context successfully scanned.")

    # -------------------------------------------------------------------------
    # Schritt 2 & 3: Discovery-Prompt senden & Fragen erhalten
    # -------------------------------------------------------------------------
    system_prompt = load_discovery_prompt()

    discovery_user_prompt = (
        "### Task: Analyze Requirements & Formulate Clarifying Questions\n\n"
        f"#### Repository Context:\n{repo_context}\n\n"
        f"#### Raw User Requirements:\n{raw_requirements.strip()}\n\n"
        "Instructions:\n"
        "1. Analyze technical implications, dependencies, and architecture.\n"
        "2. Identify hidden assumptions, edge cases, risks, or gaps (blind spots).\n"
        "3. Provide a concise summary of your understanding.\n"
        "4. Formulate 3 to 7 precise, numbered questions to resolve all critical ambiguities."
    )

    with ui_console.status("[cyan]Agent is analyzing requirements and blind spots...", spinner="dots"):
        try:
            raw_analysis = await provider.complete(
                prompt=discovery_user_prompt,
                system_prompt=system_prompt,
                json_schema=DiscoveryAnalysis,
            )
            analysis = _parse_discovery_analysis(raw_analysis)
        except Exception as exc:
            logger.error("Failed to execute discovery analysis: %s", exc)
            raise DiscoveryError(f"Discovery phase failed during LLM analysis: {exc}") from exc

    # -------------------------------------------------------------------------
    # Schritt 4: Fragen im Terminal anzeigen & Benutzer nach Antworten fragen
    # -------------------------------------------------------------------------
    ui_console.print()
    ui_console.print(
        Panel(
            Markdown(analysis.summary),
            title="[bold green]Anforderungs-Zusammenfassung[/bold green]",
            border_style="green",
        )
    )

    if analysis.blind_spots:
        spots_markdown = "\n".join(f"- {spot}" for spot in analysis.blind_spots)
        ui_console.print(
            Panel(
                Markdown(spots_markdown),
                title="[bold yellow]Aufgedeckte blinde Flecken & Risiken[/bold yellow]",
                border_style="yellow",
            )
        )

    answers: dict[str, str] = {}
    questions = analysis.questions

    if questions:
        ui_console.print("\n[bold cyan]Bitte beantworte die folgenden Fragen zur Spezifikation:[/bold cyan]\n")
        for idx, question in enumerate(questions, start=1):
            ui_console.print(f"[bold cyan]Frage {idx}:[/bold cyan] {question}")

            prompt_label = f"  Antwort zu Frage {idx}"
            if input_func is not None:
                user_answer = input_func(f"{prompt_label}: ").strip()
            else:
                user_answer = Prompt.ask(f"[bold]{prompt_label}[/bold]", console=ui_console).strip()

            if not user_answer:
                user_answer = "(Standard / Keine Angabe)"
            answers[f"Frage {idx}: {question}"] = user_answer
            ui_console.print()
    else:
        # Falls keine Fragen aufgetreten sind
        ui_console.print("[dim]Keine offenen Fragen identifiziert. Fahre direkt mit der Spezifikation fort.[/dim]")

    # -------------------------------------------------------------------------
    # Schritt 5: Antworten an Agenten schicken & finale SPEC.md generieren
    # -------------------------------------------------------------------------
    qa_blocks = "\n".join(f"- **{q}**\n  *Antwort:* {a}" for q, a in answers.items()) or "None provided."

    spec_gen_prompt = (
        "### Task: Generate Final SPEC.md\n\n"
        f"#### Repository Context:\n{repo_context}\n\n"
        f"#### Raw Requirements:\n{raw_requirements.strip()}\n\n"
        f"#### User Clarifications & Decisions:\n{qa_blocks}\n\n"
        "Instructions:\n"
        "Generate the complete, authoritative `SPEC.md` document in GitHub-flavored Markdown. "
        "Strictly adhere to the following structure:\n"
        "1. Overview & Business Goal\n"
        "2. Technical Context & Stack Impact\n"
        "3. Interfaces & Contracts\n"
        "4. Acceptance Criteria (use checklist - [ ])\n"
        "5. Edge Cases & Error Handling\n"
        "6. Security, Performance & Non-Functional Requirements\n"
        "7. Out of Scope\n\n"
        "Return the SPEC.md content directly as clean Markdown."
    )

    with ui_console.status("[cyan]Generating comprehensive SPEC.md...", spinner="dots"):
        try:
            spec_response = await provider.complete(
                prompt=spec_gen_prompt,
                system_prompt=system_prompt,
                json_schema=None,
            )
            spec_markdown = _clean_markdown_fence(str(spec_response))
        except Exception as exc:
            logger.error("Failed to generate SPEC.md: %s", exc)
            raise DiscoveryError(f"Failed to generate SPEC.md: {exc}") from exc

    # -------------------------------------------------------------------------
    # Schritt 6: Terminal-Freigabe-Prompt ([A]pprove / [E]dit / [C]ancel)
    # -------------------------------------------------------------------------
    spec_target_path = repo_path / "SPEC.md"

    while True:
        ui_console.print()
        ui_console.print(
            Panel(
                Markdown(spec_markdown),
                title=f"[bold magenta]Vorschau: {spec_target_path.name}[/bold magenta]",
                subtitle=f"[dim]Geplanter Pfad: {spec_target_path.resolve()}[/dim]",
                border_style="magenta",
            )
        )

        ui_console.print(
            "\n[bold]Möchtest du diese Spezifikation freigeben?[/bold]\n"
            "  [bold green][A][/bold green]pprove & Start Dev  |  "
            "[bold yellow][E][/bold yellow]dit  |  "
            "[bold red][C][/bold red]ancel\n"
        )

        if input_func is not None:
            choice = input_func("Auswahl [A/E/C]: ").strip().lower()
        else:
            choice = Prompt.ask(
                "[bold]Auswahl[/bold]",
                choices=["a", "approve", "e", "edit", "c", "cancel"],
                default="a",
                console=ui_console,
            ).strip().lower()

        if choice in ("a", "approve"):
            try:
                repo_path.mkdir(parents=True, exist_ok=True)
                spec_target_path.write_text(spec_markdown, encoding="utf-8")
                ui_console.print(
                    Panel(
                        f"[bold green]✓ SPEC.md erfolgreich gespeichert unter:[/bold green]\n{spec_target_path.resolve()}\n\n"
                        "[cyan]Bereit für Phase 4: Coding Agent.[/cyan]",
                        title="[bold green]Spezifikation Freigegeben[/bold green]",
                        border_style="green",
                    )
                )
                return spec_markdown
            except Exception as write_err:
                logger.error("Could not write SPEC.md to '%s': %s", spec_target_path, write_err)
                raise DiscoveryError(f"Failed to save SPEC.md: {write_err}") from write_err

        elif choice in ("e", "edit"):
            ui_console.print("[yellow]Bitte gib dein Feedback oder deine Änderungswünsche ein:[/yellow]")
            if input_func is not None:
                feedback = input_func("Feedback: ").strip()
            else:
                feedback = Prompt.ask("[bold yellow]Feedback[/bold yellow]", console=ui_console).strip()

            if not feedback:
                ui_console.print("[dim]Kein Feedback eingegeben. Wiederhole Auswahl.[/dim]")
                continue

            refine_prompt = (
                "### Task: Revise SPEC.md based on user feedback\n\n"
                f"#### Current SPEC.md:\n{spec_markdown}\n\n"
                f"#### User Feedback & Change Requests:\n{feedback}\n\n"
                "Please update the SPEC.md accordingly and return the full updated Markdown document."
            )

            with ui_console.status("[cyan]Updating SPEC.md according to your feedback...", spinner="dots"):
                try:
                    revised_spec = await provider.complete(
                        prompt=refine_prompt,
                        system_prompt=system_prompt,
                        json_schema=None,
                    )
                    spec_markdown = _clean_markdown_fence(str(revised_spec))
                except Exception as exc:
                    logger.error("Failed to revise SPEC.md: %s", exc)
                    ui_console.print(f"[red]Fehler bei der Überarbeitung: {exc}[/red]")

        elif choice in ("c", "cancel"):
            ui_console.print(
                Panel(
                    "[bold red]Discovery-Workflow durch Benutzer abgebrochen.[/bold red]",
                    border_style="red",
                )
            )
            raise DiscoveryCancelledError("Discovery workflow was cancelled by user.")
        else:
            ui_console.print("[red]Ungültige Auswahl. Bitte 'A', 'E' oder 'C' wählen.[/red]")
