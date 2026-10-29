"""Consensus & Auto-Merge controller for MergeFlow orchestrator (Phase 7).

Coordinates Phase 7 of the orchestration workflow:
1. Evaluates code review results across all 4 personas (functional, architecture, security, qa_testing).
2. Fall A: If any review is not APPROVED or open BLOCKER / WARNING findings exist:
   - Formats all findings into a structured Markdown document with an embedded JSON representation.
   - Checks the re-review retry limit (maximum 3 iterations by default).
   - If retry limit exceeded, triggers the circuit breaker, transitioning state to FAILED.
   - If within limit, increments retry count, updates findings, and signals re-entry to Phase 4 (Dev-Loop).
3. Fall B: If all 4 reviewers have APPROVED and no BLOCKER / WARNING findings remain:
   - Inspects the auto_merge configuration flag:
     - If auto_merge == True: executes `gh pr merge --auto --squash` or `glab mr merge --auto --squash`.
     - If auto_merge == False: displays a terminal review summary and prompts:
       'Alle Checks & Reviews bestanden. Merge jetzt ausführen? [y/N]'.
     - Transitions state to DONE.
"""

from collections.abc import Callable
from enum import Enum
import json
import logging
from pathlib import Path
import re
from typing import Any, Sequence

from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.config import AppConfig
from core.models import Finding, ReviewResult, ReviewSummary, Severity, WorkflowPhase, WorkflowState
from vcs.remote_vcs import RemoteVCS, RemoteVCSError

logger = logging.getLogger(__name__)

DEFAULT_MAX_REVIEW_RETRIES = 3


class ConsensusStatus(str, Enum):
    """Consensus evaluation outcome status."""

    APPROVED = "APPROVED"
    NEEDS_REVISION = "NEEDS_REVISION"
    REJECTED = "REJECTED"


class ConsensusError(Exception):
    """Base exception for consensus and merge phase errors."""


class ConsensusCircuitBreakerError(ConsensusError):
    """Raised when re-review retry limit is reached."""


class ConsensusDecision(BaseModel):
    """Structured decision outcome of Phase 7 consensus evaluation."""

    status: ConsensusStatus = Field(
        ...,
        description="Consensus status: APPROVED, NEEDS_REVISION, or REJECTED.",
    )
    approved: bool = Field(
        ...,
        description="Whether consensus approval was reached (all approved, 0 blockers, 0 warnings).",
    )
    has_blockers: bool = Field(
        default=False,
        description="Whether any BLOCKER finding exists.",
    )
    has_warnings: bool = Field(
        default=False,
        description="Whether any WARNING finding exists.",
    )
    total_findings: int = Field(
        default=0,
        description="Total count of findings across all reviews.",
    )
    blockers: list[Finding] = Field(
        default_factory=list,
        description="List of unresolved blocker findings.",
    )
    warnings: list[Finding] = Field(
        default_factory=list,
        description="List of unresolved warning findings.",
    )
    nits: list[Finding] = Field(
        default_factory=list,
        description="List of unresolved nit findings.",
    )
    feedback_markdown: str = Field(
        default="",
        description="Structured Markdown remediation feedback for the developer agent.",
    )
    feedback_json: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured JSON representation of all review findings.",
    )
    merged: bool = Field(
        default=False,
        description="Whether pull request was successfully merged.",
    )
    merge_mode: str | None = Field(
        default=None,
        description="Merge execution mode: 'auto', 'manual', or None.",
    )
    message: str = Field(
        default="",
        description="Explanatory message or decision summary.",
    )


def extract_pr_number_from_url(url: str | None) -> int | None:
    """Extract numeric PR/MR ID from a GitHub or GitLab URL.

    Args:
        url: PR or MR web URL.

    Returns:
        Integer PR/MR number, or None if not found.
    """
    if not url:
        return None
    match = re.search(r"/(?:pull|merge_requests)/(\d+)", url)
    return int(match.group(1)) if match else None


def evaluate_review_consensus(
    reviews_or_summary: Sequence[ReviewResult] | ReviewSummary,
    required_reviewers_count: int = 4,
) -> tuple[bool, list[Finding], list[Finding], list[Finding], list[ReviewResult]]:
    """Evaluate whether reviews satisfy strict consensus criteria.

    Consensus approval requires:
    1. At least `required_reviewers_count` reviews present (or all present if less configured).
    2. Every reviewer persona has status == "APPROVED".
    3. Exactly zero findings with severity == BLOCKER.
    4. Exactly zero findings with severity == WARNING.

    Args:
        reviews_or_summary: Sequence of ReviewResult objects or an aggregated ReviewSummary.
        required_reviewers_count: Expected number of distinct persona reviews.

    Returns:
        Tuple of (is_approved, blockers, warnings, nits, reviews_list).
    """
    if isinstance(reviews_or_summary, ReviewSummary):
        reviews_list = list(reviews_or_summary.reviews)
        all_findings = [f for r in reviews_list for f in r.findings]
    else:
        reviews_list = list(reviews_or_summary)
        all_findings = [f for r in reviews_list for f in r.findings]

    blockers = [f for f in all_findings if f.severity == Severity.BLOCKER]
    warnings = [f for f in all_findings if f.severity == Severity.WARNING]
    nits = [f for f in all_findings if f.severity == Severity.NIT]

    has_blockers = len(blockers) > 0
    has_warnings = len(warnings) > 0
    all_status_approved = len(reviews_list) > 0 and all(r.status == "APPROVED" for r in reviews_list)

    # Check minimum reviewer count if provided
    count_sufficient = len(reviews_list) >= required_reviewers_count if required_reviewers_count > 0 else len(reviews_list) > 0

    is_approved = all_status_approved and not has_blockers and not has_warnings and count_sufficient

    return (is_approved, blockers, warnings, nits, reviews_list)


def format_feedback_json(
    reviews: Sequence[ReviewResult],
    blockers: Sequence[Finding],
    warnings: Sequence[Finding],
    nits: Sequence[Finding],
) -> dict[str, Any]:
    """Format review findings into a structured JSON dictionary.

    Args:
        reviews: List of ReviewResult objects.
        blockers: Consolidated BLOCKER findings.
        warnings: Consolidated WARNING findings.
        nits: Consolidated NIT findings.

    Returns:
        Structured dictionary representation of review findings.
    """
    return {
        "status": "CHANGES_REQUESTED",
        "total_findings": len(blockers) + len(warnings) + len(nits),
        "reviewers": [
            {
                "reviewer": r.reviewer,
                "status": r.status,
                "summary": r.summary,
                "findings_count": len(r.findings),
            }
            for r in reviews
        ],
        "findings": {
            "blockers": [f.model_dump() for f in blockers],
            "warnings": [f.model_dump() for f in warnings],
            "nits": [f.model_dump() for f in nits],
        },
    }


def format_review_feedback(
    reviews: Sequence[ReviewResult],
    blockers: Sequence[Finding],
    warnings: Sequence[Finding],
    nits: Sequence[Finding],
) -> str:
    """Format review findings into a structured Markdown document with embedded JSON.

    Args:
        reviews: List of ReviewResult objects.
        blockers: Consolidated BLOCKER findings.
        warnings: Consolidated WARNING findings.
        nits: Consolidated NIT findings.

    Returns:
        Comprehensive Markdown document containing findings and embedded JSON schema block.
    """
    json_data = format_feedback_json(reviews, blockers, warnings, nits)
    json_str = json.dumps(json_data, indent=2)

    lines = [
        "# Code Review Remediation Feedback",
        "",
        "**Status:** `CHANGES_REQUESTED`",
        f"**Blockers:** {len(blockers)} | **Warnings:** {len(warnings)} | **Nits:** {len(nits)}",
        "",
        "## Reviewer Decisions",
        "",
        "| Reviewer | Status | Findings | Summary |",
        "|---|---|---|---|",
    ]

    for r in reviews:
        lines.append(f"| **{r.reviewer}** | `{r.status}` | {len(r.findings)} | {r.summary} |")

    lines.append("")

    if blockers:
        lines.append("## Blocker Findings (Must Be Resolved Immediately)")
        for f in blockers:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"- **[{loc}]** {f.issue}")
            if f.suggestion:
                lines.append(f"  *Fix Suggestion:* {f.suggestion}")
        lines.append("")

    if warnings:
        lines.append("## Warning Findings (Must Be Addressed)")
        for f in warnings:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"- **[{loc}]** {f.issue}")
            if f.suggestion:
                lines.append(f"  *Fix Suggestion:* {f.suggestion}")
        lines.append("")

    if nits:
        lines.append("## Minor / Nit Findings")
        for f in nits:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"- **[{loc}]** {f.issue}")
            if f.suggestion:
                lines.append(f"  *Fix Suggestion:* {f.suggestion}")
        lines.append("")

    lines.append("## Structured Findings Data (JSON)")
    lines.append("```json")
    lines.append(json_str)
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


def display_consensus_summary(
    reviews: Sequence[ReviewResult],
    console: Console | None = None,
) -> None:
    """Print an attractive summary table to the terminal using Rich.

    Args:
        reviews: List of ReviewResult objects.
        console: Optional Rich Console.
    """
    ui_console = console or Console()

    table = Table(title="[bold green]✓ Alle Checks & Multi-Persona Reviews Bestanden[/bold green]")
    table.add_column("Reviewer Persona", style="cyan", no_wrap=True)
    table.add_column("Status", style="bold green")
    table.add_column("Findings", justify="right")
    table.add_column("Summary", style="dim")

    for r in reviews:
        table.add_row(
            r.reviewer,
            r.status,
            str(len(r.findings)),
            r.summary,
        )

    ui_console.print()
    ui_console.print(table)
    ui_console.print()


async def run_consensus_phase(
    summary: ReviewSummary | Sequence[ReviewResult],
    state: WorkflowState,
    config: AppConfig | None = None,
    remote_vcs: RemoteVCS | None = None,
    auto_merge: bool | None = None,
    max_retries: int | None = None,
    console: Console | None = None,
    input_func: Callable[[str], str] | None = None,
) -> ConsensusDecision:
    """Execute Phase 7: Consensus Evaluation, Feedback Loop & Auto-Merge.

    1. Evaluates code review results.
    2. Fall A: If any review is not APPROVED or open BLOCKER / WARNING findings exist:
       - Formats all findings into structured Markdown/JSON feedback.
       - Enforces max review retries (default: 3). If exceeded, triggers circuit breaker and fails.
       - Otherwise, increments retry count, updates state findings, and returns NEEDS_REVISION.
    3. Fall B: If all 4 reviewers APPROVED with 0 BLOCKERS and 0 WARNINGS:
       - If auto_merge == True: executes `gh pr merge --auto --squash` / `glab mr merge --auto --squash`.
       - If auto_merge == False: shows terminal summary and prompts user:
         'Alle Checks & Reviews bestanden. Merge jetzt ausführen? [y/N]'
       - Transitions state to DONE.

    Args:
        summary: Aggregated ReviewSummary or list of ReviewResult items.
        state: Active WorkflowState instance.
        config: Optional AppConfig for retrieving workflow settings.
        remote_vcs: Optional RemoteVCS instance for git hosting interactions.
        auto_merge: Optional explicit override for the auto_merge configuration.
        max_retries: Optional explicit override for max review retries (default: from config or 3).
        console: Optional Rich Console for UI rendering.
        input_func: Optional input function for mock testing user interactive prompts.

    Returns:
        ConsensusDecision describing the evaluation and merge outcome.

    Raises:
        ConsensusCircuitBreakerError: If re-review retries exceed limit.
        ConsensusError: If remote VCS merge operation fails.
    """
    ui_console = console or Console()

    # Determine retry limit and working directory
    resolved_max_retries = (
        max_retries
        if max_retries is not None
        else (config.workflow.review_max_retries if config else DEFAULT_MAX_REVIEW_RETRIES)
    )
    repo_path = config.workflow.working_directory if config else "."
    vcs = remote_vcs or RemoteVCS(repo_path=repo_path)

    # Transition state to CONSENSUS phase
    state.transition_to(WorkflowPhase.CONSENSUS)

    # Evaluate consensus
    is_approved, blockers, warnings, nits, reviews_list = evaluate_review_consensus(summary)

    # -------------------------------------------------------------------------
    # Fall A: Non-approved reviews or open BLOCKER / WARNING findings
    # -------------------------------------------------------------------------
    if not is_approved:
        logger.warning(
            "Consensus NOT reached: All reviews approved=%s, Blockers=%d, Warnings=%d",
            all(r.status == "APPROVED" for r in reviews_list),
            len(blockers),
            len(warnings),
        )

        feedback_md = format_review_feedback(reviews_list, blockers, warnings, nits)
        feedback_json = format_feedback_json(reviews_list, blockers, warnings, nits)

        # Circuit-Breaker Check
        if state.review_retry_count >= resolved_max_retries:
            logger.error(
                "CIRCUIT-BREAKER TRIGGERED: Maximum review retries (%d) exceeded. Aborting workflow.",
                resolved_max_retries,
            )
            state.transition_to(WorkflowPhase.FAILED)
            state.metadata["review_circuit_breaker"] = True
            state.metadata["unresolved_blockers"] = len(blockers)
            state.metadata["unresolved_warnings"] = len(warnings)

            # Record diagnostic finding in state
            failure_finding = Finding(
                file="code-review:consensus",
                severity=Severity.BLOCKER,
                issue=(
                    f"Code review consensus failed after {resolved_max_retries} remediation iterations. "
                    f"Open findings: {len(blockers)} blocker(s), {len(warnings)} warning(s)."
                ),
                suggestion="Investigate reviewer feedback in state and resolve remaining issues manually.",
            )
            state.add_finding(failure_finding)

            ui_console.print(
                Panel(
                    f"[bold red]Circuit-Breaker ausgelöst:[/bold red] Maximales Re-Review-Limit "
                    f"({resolved_max_retries}) erreicht.\n"
                    f"Offene Blocker: {len(blockers)} | Offene Warnings: {len(warnings)}",
                    title="[bold red]Review-Schleife Abgebrochen[/bold red]",
                    border_style="red",
                )
            )

            return ConsensusDecision(
                status=ConsensusStatus.REJECTED,
                approved=False,
                has_blockers=len(blockers) > 0,
                has_warnings=len(warnings) > 0,
                total_findings=len(blockers) + len(warnings) + len(nits),
                blockers=blockers,
                warnings=warnings,
                nits=nits,
                feedback_markdown=feedback_md,
                feedback_json=feedback_json,
                merged=False,
                message=f"Circuit-breaker triggered: exceeded {resolved_max_retries} review retries.",
            )

        # Increment review retry counter and prepare state for dev loop remediation
        new_count = state.increment_review_retry()
        logger.info(
            "Initiating review remediation loop (%d/%d)...",
            new_count,
            resolved_max_retries,
        )

        # Update state active findings
        state.clear_findings()
        for f in blockers + warnings + nits:
            state.add_finding(f)

        ui_console.print(
            Panel(
                f"[bold yellow]Kritikpunkte festgestellt (Runde {new_count}/{resolved_max_retries}):[/bold yellow]\n"
                f"Blocker: {len(blockers)} | Warnings: {len(warnings)} | Nits: {len(nits)}\n\n"
                "[cyan]Übergebe Findings an Dev-Agenten zur Nachbesserung (Phasen 4 -> 5 -> 6)...[/cyan]",
                title="[bold yellow]Re-Review Schleife[/bold yellow]",
                border_style="yellow",
            )
        )

        return ConsensusDecision(
            status=ConsensusStatus.NEEDS_REVISION,
            approved=False,
            has_blockers=len(blockers) > 0,
            has_warnings=len(warnings) > 0,
            total_findings=len(blockers) + len(warnings) + len(nits),
            blockers=blockers,
            warnings=warnings,
            nits=nits,
            feedback_markdown=feedback_md,
            feedback_json=feedback_json,
            merged=False,
            message=f"Revision required. Iteration {new_count}/{resolved_max_retries}.",
        )

    # -------------------------------------------------------------------------
    # Fall B: All 4 Reviewers have APPROVED and 0 Blockers, 0 Warnings
    # -------------------------------------------------------------------------
    logger.info("Consensus REACHED: All review personas approved and no blockers/warnings.")

    # Determine auto_merge flag
    effective_auto_merge = (
        auto_merge
        if auto_merge is not None
        else (config.workflow.auto_merge if config else False)
    )

    # Extract PR / MR number if available
    pr_number = state.metadata.get("pr_number")
    if pr_number is None and "pr_url" in state.metadata:
        pr_number = extract_pr_number_from_url(state.metadata["pr_url"])

    display_consensus_summary(reviews_list, console=ui_console)

    if effective_auto_merge:
        # Auto-merge enabled
        ui_console.print(
            "[bold green]Konfigurationsflag 'auto_merge: true' aktiv. Führe automatischen Merge aus...[/bold green]"
        )
        try:
            exit_code, stdout, stderr = await vcs.merge_pr(
                pr_number=pr_number,
                auto=True,
                squash=True,
            )
            state.metadata["merged"] = True
            state.metadata["merge_mode"] = "auto"
            state.metadata["merge_output"] = stdout.strip() or stderr.strip()
            state.transition_to(WorkflowPhase.DONE)

            ui_console.print(
                Panel(
                    "[bold green]✓ PR / MR erfolgreich via Auto-Squash gemerged![/bold green]",
                    title="[bold green]Auto-Merge Erfolgreich[/bold green]",
                    border_style="green",
                )
            )

            return ConsensusDecision(
                status=ConsensusStatus.APPROVED,
                approved=True,
                has_blockers=False,
                has_warnings=False,
                total_findings=len(nits),
                nits=nits,
                merged=True,
                merge_mode="auto",
                message="PR successfully auto-merged with squash.",
            )
        except Exception as exc:
            logger.error("Auto-merge execution failed: %s", exc)
            state.metadata["merge_error"] = str(exc)
            raise ConsensusError(f"Auto-merge failed: {exc}") from exc

    else:
        # Manual confirmation prompt
        prompt_text = "Alle Checks & Reviews bestanden. Merge jetzt ausführen? [y/N]: "
        if input_func is not None:
            user_choice = input_func(prompt_text).strip().lower()
        else:
            try:
                user_choice = input(prompt_text).strip().lower()
            except (EOFError, KeyboardInterrupt):
                user_choice = "n"

        should_merge = user_choice in ("y", "yes")

        if should_merge:
            ui_console.print("[cyan]Führe Merge aus...[/cyan]")
            try:
                exit_code, stdout, stderr = await vcs.merge_pr(
                    pr_number=pr_number,
                    auto=False,
                    squash=True,
                )
                state.metadata["merged"] = True
                state.metadata["merge_mode"] = "manual"
                state.metadata["merge_output"] = stdout.strip() or stderr.strip()
                state.transition_to(WorkflowPhase.DONE)

                ui_console.print(
                    Panel(
                        "[bold green]✓ PR / MR erfolgreich nach Benutzerbestätigung gemerged![/bold green]",
                        title="[bold green]Merge Erfolgreich[/bold green]",
                        border_style="green",
                    )
                )

                return ConsensusDecision(
                    status=ConsensusStatus.APPROVED,
                    approved=True,
                    has_blockers=False,
                    has_warnings=False,
                    total_findings=len(nits),
                    nits=nits,
                    merged=True,
                    merge_mode="manual",
                    message="PR successfully merged after user confirmation.",
                )
            except Exception as exc:
                logger.error("Manual merge execution failed: %s", exc)
                state.metadata["merge_error"] = str(exc)
                raise ConsensusError(f"Merge operation failed: {exc}") from exc
        else:
            logger.info("Merge skipped by user decision.")
            state.metadata["merged"] = False
            state.metadata["merge_skipped_by_user"] = True
            state.transition_to(WorkflowPhase.DONE)

            ui_console.print(
                Panel(
                    "[yellow]Alle Checks bestanden. Merge wurde auf Benutzerwunsch übersprungen.[/yellow]",
                    title="[bold yellow]Merge Übersprungen[/bold yellow]",
                    border_style="yellow",
                )
            )

            return ConsensusDecision(
                status=ConsensusStatus.APPROVED,
                approved=True,
                has_blockers=False,
                has_warnings=False,
                total_findings=len(nits),
                nits=nits,
                merged=False,
                merge_mode="manual",
                message="All checks passed. Merge skipped by user.",
            )
