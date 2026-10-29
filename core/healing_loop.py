"""Self-Healing CI Loop controller for MergeFlow orchestrator (Phase 5).

Coordinates autonomous pipeline monitoring and self-healing remediation:
1. Polls remote pipeline status via CIWatcher until completion or failure.
2. If pipeline succeeds, successfully transitions phase to REVIEW and exits.
3. If pipeline fails and retry_count < max_retries:
   - Increments retry_count.
   - Provides failure logs and recent commit history to the CI Healing Agent.
   - Executes agent remediation actions strictly validated through Gatekeeper.
   - Verifies tests locally and commits with `fix(ci): <reason>`.
   - Pushes fixes to remote branch and repeats polling.
4. If retry_count reaches max_retries (Circuit-Breaker):
   - Transitions state to FAILED.
   - Records diagnostics and alerts the user to prevent runaway loops.
"""

import asyncio
from collections.abc import Callable
import logging
from pathlib import Path
import re
from typing import Any

from rich.console import Console
from rich.panel import Panel

from core.dev_loop import DevActionType, DevAgentStep
from core.discovery import gather_repo_context
from core.models import Finding, FindingSeverity, Severity, WorkflowPhase, WorkflowState
from core.provider import BaseLLMProvider
from core.skills import SkillManager
from security.gatekeeper import Gatekeeper
from security.shell_runner import ShellRunner
from vcs.ci_watcher import CIWatcher, PipelineResult
from vcs.git_handler import GitHandler
from vcs.remote_vcs import RemoteVCS

logger = logging.getLogger(__name__)

DEFAULT_CI_HEALING_PROMPT_PATH = Path("prompts/ci_healing.md")


class CIHealingError(Exception):
    """Exception raised for fatal CI healing execution errors."""


def load_ci_healing_prompt(
    prompt_path: Path | str | None = None,
    skill_manager: SkillManager | None = None,
    custom_skills: list[str] | None = None,
) -> str:
    """Load the CI healing agent system prompt from disk.

    Injects phase-specific domain skills via {{PHASE_SKILLS}} placeholder.

    Args:
        prompt_path: Optional custom prompt path (defaults to prompts/ci_healing.md).
        skill_manager: Optional SkillManager instance for injecting modular skills.
        custom_skills: Optional additional project-specific skill files.

    Returns:
        System prompt string with injected skills.
    """
    path = Path(prompt_path) if prompt_path else DEFAULT_CI_HEALING_PROMPT_PATH
    if not path.is_file():
        raw_prompt = (
            "You are the Autonomous CI Healing Agent of MergeFlow. "
            "Analyze the failure logs, fix the broken files, test locally, and commit with 'fix(ci): <reason>'."
        )
    else:
        raw_prompt = path.read_text(encoding="utf-8")

    manager = skill_manager or SkillManager()
    return manager.inject_skills(raw_prompt, phase="dev", custom_skills=custom_skills)


def format_ci_commit_message(
    message: str | None,
    default_reason: str = "resolve pipeline failure",
) -> str:
    """Format and guarantee that a commit message uses the Conventional Commit prefix 'fix(ci):'.

    Args:
        message: Raw commit message or reason.
        default_reason: Fallback reason text if message is empty.

    Returns:
        Properly formatted Conventional Commit message starting with 'fix(ci):'.
    """
    cleaned = (message or default_reason).strip()
    if cleaned.startswith("fix(ci):"):
        return cleaned
    if cleaned.startswith("fix(ci)"):
        # e.g. fix(ci)!: or fix(ci) -
        return f"fix(ci): {cleaned[7:].lstrip('!:- ')}"
    if cleaned.startswith("fix:"):
        return f"fix(ci): {cleaned[4:].strip()}"
    return f"fix(ci): {cleaned}"


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


async def _execute_healing_step(
    step: DevAgentStep,
    repo_root: Path,
    gatekeeper: Gatekeeper,
    shell_runner: ShellRunner,
    git_handler: GitHandler,
    state: WorkflowState | None = None,
) -> tuple[bool, str]:
    """Execute a single step from the CI healing agent.

    All shell operations and file writes strictly pass through Gatekeeper policy.

    Args:
        step: Action to perform.
        repo_root: Root path of repository sandbox.
        gatekeeper: Active Gatekeeper instance.
        shell_runner: Active ShellRunner instance.
        git_handler: Active GitHandler instance.
        state: Optional active WorkflowState instance.

    Returns:
        Tuple of (success: bool, outcome_message: str).
    """
    if step.action_type == DevActionType.RUN_COMMAND:
        cmd = (step.command or "").strip()
        if not cmd:
            return False, "Error: No command specified for RUN_COMMAND action."

        res = await shell_runner.run_command(
            command=cmd,
            cwd=repo_root,
            task_goal=step.thought or "CI Healing Step Execution",
            state=state,
        )

        if res.was_blocked:
            return False, f"Command BLOCKED by Gatekeeper: {res.block_reason}"

        status_text = f"Exit code: {res.exit_code}\n"
        if res.stdout:
            status_text += f"STDOUT:\n{res.stdout}\n"
        if res.stderr:
            status_text += f"STDERR:\n{res.stderr}\n"
        return res.exit_code == 0, status_text.strip()

    elif step.action_type == DevActionType.WRITE_FILE:
        file_path_str = (step.file_path or "").strip()
        if not file_path_str:
            return False, "Error: No file_path specified for WRITE_FILE action."

        target_file = (repo_root / file_path_str).resolve()

        # Gatekeeper policy: ensure target file is strictly within repo_root
        if not target_file.is_relative_to(repo_root):
            return False, f"Write BLOCKED by Gatekeeper: path '{file_path_str}' escapes repository root."

        # Gatekeeper policy: check for directory traversal / escapes
        escape_reason = gatekeeper.check_path_escapes(str(file_path_str), repo_root)
        if escape_reason:
            return False, f"Write BLOCKED by Gatekeeper: {escape_reason}"

        # Additional sensitive path protections (.env, .git)
        clean_norm = file_path_str.replace("\\", "/")
        if clean_norm == ".env" or clean_norm.startswith(".env.") or ".git" in clean_norm.split("/"):
            return False, f"Write BLOCKED by Gatekeeper: modifying '{file_path_str}' is forbidden."

        try:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(step.file_content or "", encoding="utf-8")
            return True, f"Successfully wrote {len(step.file_content or '')} characters to '{file_path_str}'."
        except Exception as exc:
            return False, f"Failed to write file '{file_path_str}': {exc}"

    elif step.action_type == DevActionType.COMMIT:
        # Scope-Drift Guard
        try:
            is_drift, drift_reason = await git_handler.check_scope_drift()
            if is_drift:
                logger.warning("Scope-Drift-Guard Warnung vor CI-Healing Commit: %s", drift_reason)
        except Exception as drift_err:
            logger.debug("Scope drift check error: %s", drift_err)

        raw_msg = (step.commit_message or step.thought or "fix ci error").strip()
        formatted_msg = format_ci_commit_message(raw_msg)
        try:
            commit_hash = await git_handler.commit_changes(formatted_msg)
            return True, f"Successfully committed changes with '{formatted_msg}': {commit_hash}"
        except Exception as exc:
            return False, f"Commit failed: {exc}"

    elif step.action_type == DevActionType.COMPLETE:
        return True, "Healing agent declared remediation complete."

    return False, f"Unknown action type: {step.action_type}"


async def run_ci_healing_loop(
    state: WorkflowState,
    max_retries: int = 3,
    gatekeeper: Gatekeeper | None = None,
    provider: BaseLLMProvider | None = None,
    git_handler: GitHandler | None = None,
    remote_vcs: RemoteVCS | None = None,
    ci_watcher: CIWatcher | None = None,
    poll_interval: int = 15,
    timeout: int = 600,
    agent_max_iterations: int = 10,
    skill_manager: SkillManager | None = None,
    custom_skills: list[str] | None = None,
    console: Console | None = None,
    input_func: Callable[[str], str] | None = None,
) -> bool:
    """Execute Phase 5: CI-Watcher & Self-Healing Loop with Circuit-Breaker.

    1. Polls the remote pipeline via CIWatcher until completion or failure.
    2. If green: completes Phase 5 successfully and transitions to REVIEW.
    3. If red and retry_count < max_retries:
       - Increments retry_count.
       - Dispatches filtered failure logs and recent commit log to the Healing Agent.
       - The Healing Agent analyzes, modifies files via Gatekeeper, runs local verification tests,
         and commits with 'fix(ci): <reason>'.
       - Pushes fixes to the remote branch and polls again.
    4. If retry_count reaches max_retries (Circuit-Breaker):
       - Aborts automatic loop immediately.
       - Transitions state to HUMAN_INTERVENTION_REQUIRED.
       - Prints consolidated conflict report and offers interactive options [M]/[F]/[A].

    Args:
        state: Active WorkflowState tracking current task and counters.
        max_retries: Maximum number of healing attempts allowed before tripping the circuit-breaker.
        gatekeeper: Optional custom Gatekeeper instance.
        provider: Optional LLM provider for healing agent completions.
        git_handler: Optional custom GitHandler.
        remote_vcs: Optional custom RemoteVCS client.
        ci_watcher: Optional custom CIWatcher.
        poll_interval: Polling frequency in seconds.
        timeout: Maximum time in seconds to wait per pipeline run.
        agent_max_iterations: Maximum iteration turns per healing agent remediation cycle.
        skill_manager: Optional SkillManager.
        custom_skills: Optional custom skills list.
        console: Optional Rich Console for UI output.
        input_func: Optional input function for terminal prompt testing.

    Returns:
        True if the pipeline eventually succeeds; False if circuit-breaker trips or fails.
    """
    repo_root = gatekeeper.repo_root if gatekeeper else Path(".").resolve()
    active_gatekeeper = gatekeeper or Gatekeeper(repo_root=repo_root)
    active_git = git_handler or GitHandler(repo_path=repo_root)
    active_vcs = remote_vcs or RemoteVCS(repo_path=repo_root)
    active_watcher = ci_watcher or CIWatcher(remote_vcs=active_vcs, repo_path=repo_root)
    shell_runner = ShellRunner(gatekeeper=active_gatekeeper, repo_root=repo_root)
    ui_console = console or Console()

    # Ensure active working branch is recorded
    if not state.branch_name:
        state.branch_name = await active_git.get_current_branch()

    # Extract PR / MR number if available in metadata
    pr_number = state.metadata.get("pr_number")
    if pr_number is None and "pr_url" in state.metadata:
        pr_number = extract_pr_number_from_url(state.metadata["pr_url"])

    state.transition_to(WorkflowPhase.CI_LOOP)
    logger.info(
        "Starting CI-Watcher & Self-Healing Loop for branch '%s' (max_retries=%d, pr=%s)",
        state.branch_name,
        max_retries,
        pr_number,
    )

    while True:
        # Step 1: Poll remote pipeline status
        pipeline_result = await active_watcher.wait_for_pipeline(
            poll_interval=poll_interval,
            timeout=timeout,
            pr_number=pr_number,
            branch=state.branch_name,
        )

        # Step 2: Pipeline is green
        if pipeline_result.passed:
            logger.info("CI pipeline passed! Phase 5 Self-Healing completed successfully.")
            state.transition_to(WorkflowPhase.REVIEW)
            return True

        # Step 3: Pipeline is red / failed
        logger.warning(
            "CI pipeline failed on branch '%s'. Retry attempt %d of %d.",
            state.branch_name,
            state.ci_retry_count,
            max_retries,
        )

        # Circuit-Breaker check
        if state.ci_retry_count >= max_retries:
            logger.error(
                "CIRCUIT-BREAKER TRIGGERED: Exceeded maximum CI retries (%d). Aborting healing loop.",
                max_retries,
            )
            state.transition_to(WorkflowPhase.HUMAN_INTERVENTION_REQUIRED)
            state.metadata["circuit_breaker_triggered"] = True
            state.metadata["failed_phase"] = WorkflowPhase.CI_LOOP.value
            state.metadata["ci_error"] = pipeline_result.error_logs

            # Record diagnostic finding and notify user
            failure_finding = Finding(
                file=f"ci-pipeline:{state.branch_name}",
                severity=Severity.BLOCKER,
                issue=(
                    f"Remote pipeline on branch '{state.branch_name}' failed after {max_retries} remediation "
                    f"attempts. Circuit breaker triggered to prevent infinite healing cycles.\n\n"
                    f"Extracted CI Error:\n{pipeline_result.error_logs[:1200]}"
                ),
                suggestion="Investigate the pipeline logs on remote VCS and resolve issues manually.",
            )
            state.add_finding(failure_finding)

            failed_checks = [
                c.name for c in pipeline_result.checks if c.conclusion in ("failure", "timed_out", "cancelled")
            ]
            checks_info = f"\nFehlgeschlagene Jobs: {', '.join(failed_checks)}" if failed_checks else ""
            error_preview = (pipeline_result.error_logs or pipeline_result.raw_logs or "Keine Details").strip()

            conflict_report = (
                f"[bold red]CIRCUIT-BREAKER AUSGELÖST: CI-Ping-Pong-Bremse aktiviert![/bold red]\n\n"
                f"Die CI-Pipeline auf Branch '{state.branch_name}' konnte nach {max_retries} Heilungs-Runden nicht behoben werden.{checks_info}\n\n"
                f"[bold]Extrahierte Fehlermeldungen:[/bold]\n{error_preview[:1500]}\n"
            )
            ui_console.print(
                Panel(
                    conflict_report,
                    title="[bold red]Konsolidierter CI-Konflikt-Report[/bold red]",
                    border_style="red",
                )
            )

            prompt_text = "[M]anuell übernehmen / [F]orce Merge versuchen / [A]bbruch: "
            if input_func is not None:
                user_choice = input_func(prompt_text).strip().lower()
            else:
                try:
                    user_choice = input(prompt_text).strip().lower()
                except (EOFError, KeyboardInterrupt, OSError):
                    user_choice = "a"

            if user_choice in ("m", "manuell", "manual"):
                ui_console.print(
                    "[yellow]Manuelle Übernahme gewählt. Workflow pausiert in Phase 'HUMAN_INTERVENTION_REQUIRED'. "
                    "Nach manueller Fehlerbehebung kann mit '--resume' fortgesetzt werden.[/yellow]"
                )
                return False

            elif user_choice in ("f", "force", "force merge"):
                ui_console.print("[cyan]Versuche Force Merge trotz CI-Fehlern...[/cyan]")
                try:
                    pr_num = pr_number or extract_pr_number_from_url(state.metadata.get("pr_url"))
                    exit_code, stdout, stderr = await active_vcs.merge_pr(
                        pr_number=pr_num,
                        auto=False,
                        squash=True,
                    )
                    state.metadata["merged"] = True
                    state.metadata["merge_mode"] = "force"
                    state.metadata["merge_output"] = stdout.strip() or stderr.strip()
                    state.transition_to(WorkflowPhase.DONE)
                    ui_console.print(
                        Panel(
                            "[bold green]✓ Force Merge erfolgreich durchgeführt![/bold green]",
                            title="[bold green]Force Merge Erfolgreich[/bold green]",
                            border_style="green",
                        )
                    )
                    return True
                except Exception as exc:
                    logger.error("Force merge failed: %s", exc)
                    state.transition_to(WorkflowPhase.FAILED)
                    state.metadata["merge_error"] = str(exc)
                    ui_console.print(f"[bold red]Force Merge fehlgeschlagen: {exc}[/bold red]")
                    return False

            else:
                ui_console.print("[bold red]Vorgang durch Benutzer abgebrochen.[/bold red]")
                state.transition_to(WorkflowPhase.FAILED)
                return False

        if provider is None:
            raise CIHealingError(
                "Cannot perform CI self-healing: No BaseLLMProvider was supplied for the healing agent."
            )

        # Increment retry counter
        state.increment_ci_retry()
        logger.info(
            "Initiating CI Healing Agent cycle (%d/%d)...",
            state.ci_retry_count,
            max_retries,
        )

        # Gather context: recent commits, repo context, failure logs
        recent_commits = await active_git.get_recent_commits(count=5)
        repo_context = gather_repo_context(repo_root)
        system_prompt = load_ci_healing_prompt(
            skill_manager=skill_manager,
            custom_skills=custom_skills,
        )

        history: list[str] = []

        # Run healing agent iterations
        for iteration in range(1, agent_max_iterations + 1):
            logger.info("CI Healing Agent turn %d/%d", iteration, agent_max_iterations)

            turn_prompt = (
                f"### REGEL (FIX-Modus):\n"
                f"Du befindest dich im FIX-Modus. Führe KEINE Refactorings durch und ändere keine funktionierende "
                f"Geschäftslogik außerhalb des gemeldeten Fehlers. Behebe ausschließlich das gemeldete Problem.\n\n"
                f"### CI Pipeline Failure Logs:\n{pipeline_result.error_logs or pipeline_result.raw_logs}\n\n"
                f"### Recent Git Commits:\n{recent_commits}\n\n"
                f"### Active Branch:\n{state.branch_name}\n\n"
                f"### Repository Context:\n{repo_context}\n\n"
            )
            if state.spec_markdown:
                turn_prompt += f"### Specification:\n{state.spec_markdown[:1500]}\n\n"

            if history:
                turn_prompt += "### Previous Diagnostic Actions & Results:\n" + "\n".join(history[-5:]) + "\n\n"

            turn_prompt += (
                "Diagnose the failure root cause, correct files via Gatekeeper, verify with local tests, "
                "and commit with 'fix(ci): <reason>'. Output a valid JSON DevAgentStep."
            )

            try:
                step = await provider.complete(
                    prompt=turn_prompt,
                    system_prompt=system_prompt,
                    json_schema=DevAgentStep,
                )
                if not isinstance(step, DevAgentStep):
                    step = DevAgentStep.model_validate(step)
            except Exception as exc:
                logger.warning("Healing agent LLM completion error on turn %d: %s", iteration, exc)
                history.append(f"Turn {iteration} LLM Error: {exc}")
                continue

            logger.info("CI Healing Action [%s]: %s", step.action_type, step.thought)

            if step.action_type == DevActionType.COMPLETE:
                logger.info("Healing agent declared remediation complete on turn %d.", iteration)
                break

            success, outcome = await _execute_healing_step(
                step=step,
                repo_root=repo_root,
                gatekeeper=active_gatekeeper,
                shell_runner=shell_runner,
                git_handler=active_git,
                state=state,
            )

            history.append(
                f"Turn {iteration} [{step.action_type}]: {step.thought}\nOutcome (Success={success}): {outcome}"
            )

        # After healing agent steps, commit any leftover uncommitted changes
        try:
            has_uncommitted = await active_git.has_uncommitted_changes()
            if has_uncommitted:
                try:
                    is_drift, drift_reason = await active_git.check_scope_drift()
                    if is_drift:
                        logger.warning("Scope-Drift-Guard Warnung vor CI-Healing Commit: %s", drift_reason)
                except Exception as drift_err:
                    logger.debug("Scope drift check error: %s", drift_err)

                logger.info("Staging and committing remaining fixes from healing agent.")
                default_msg = format_ci_commit_message(f"remediate CI failure attempt {state.ci_retry_count}")
                await active_git.commit_changes(default_msg)

            # Push fixes to remote branch
            logger.info("Pushing healed commits to remote branch '%s'.", state.branch_name)
            await active_git.push_branch(branch=state.branch_name)
            logger.info("Pushed healed commits successfully. Re-polling pipeline...")
        except Exception as git_exc:
            logger.warning("Git commit/push encountered an issue during healing: %s", git_exc)
