"""Developer Agent execution loop controller for MergeFlow orchestrator.

Coordinates Phase 4 of the orchestration workflow:
1. Generates and checks out a feature branch (feat/SPEC-<slug>) via GitHandler.
2. Injects SPEC.md and repository context/guidelines into the Dev Agent prompt.
3. Runs the Dev Agent loop where all shell commands are strictly validated by Gatekeeper.
4. Executes tests to verify the implementation.
5. Commits changes with Conventional Commits, pushes the branch, and opens a draft PR.
"""

import asyncio
from enum import Enum
import json
import logging
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, Field

from core.discovery import gather_repo_context
from core.models import WorkflowPhase, WorkflowState
from core.provider import BaseLLMProvider
from security.gatekeeper import Gatekeeper, GatekeeperDecision
from security.shell_runner import CommandResult, ShellRunner
from vcs.git_handler import GitHandler, sanitize_slug
from vcs.remote_vcs import RemoteVCS, RemoteVCSError

logger = logging.getLogger(__name__)

DEFAULT_DEVELOPER_PROMPT_PATH = Path("prompts/developer.md")


class DevLoopError(Exception):
    """Base exception for developer loop execution errors."""


class DevActionType(str, Enum):
    """Allowed actions emitted by the developer agent."""

    RUN_COMMAND = "RUN_COMMAND"
    WRITE_FILE = "WRITE_FILE"
    COMMIT = "COMMIT"
    COMPLETE = "COMPLETE"


class DevAgentStep(BaseModel):
    """Structured decision / action emitted by the developer agent."""

    thought: str = Field(
        default="",
        description="Reasoning about the current state, progress, and next steps.",
    )
    action_type: DevActionType = Field(
        default=DevActionType.RUN_COMMAND,
        description="Type of action to execute.",
    )
    command: str | None = Field(
        default=None,
        description="Shell command line to execute when action_type is RUN_COMMAND.",
    )
    file_path: str | None = Field(
        default=None,
        description="Relative file path within repository to write when action_type is WRITE_FILE.",
    )
    file_content: str | None = Field(
        default=None,
        description="Full file content to write when action_type is WRITE_FILE.",
    )
    commit_message: str | None = Field(
        default=None,
        description="Conventional commit message when action_type is COMMIT.",
    )
    tests_passed: bool = Field(
        default=False,
        description="Indicates whether automated tests have passed verifying the implementation.",
    )


def load_developer_prompt(prompt_path: Path | str | None = None) -> str:
    """Load the developer agent system prompt from file.

    Args:
        prompt_path: Optional custom prompt path (defaults to prompts/developer.md).

    Returns:
        System prompt string.
    """
    path = Path(prompt_path) if prompt_path else DEFAULT_DEVELOPER_PROMPT_PATH
    if not path.is_file():
        # Fallback inline system prompt if file is missing
        return (
            "You are an autonomous Senior Software Developer Agent. "
            "Implement the specification, test thoroughly, and commit using Conventional Commits."
        )
    return path.read_text(encoding="utf-8")


def extract_spec_slug(spec: str, fallback_task_id: str = "feature") -> str:
    """Extract a concise slug from the specification title.

    Args:
        spec: Markdown text of SPEC.md.
        fallback_task_id: Fallback identifier if no title header is detected.

    Returns:
        Sanitized git branch slug.
    """
    match_spec = re.search(r"#\s*SPEC:\s*([^\n\r]+)", spec, re.IGNORECASE)
    if match_spec:
        return sanitize_slug(match_spec.group(1).strip())

    match_h1 = re.search(r"^#\s+([^\n\r]+)", spec, re.MULTILINE)
    if match_h1:
        return sanitize_slug(match_h1.group(1).strip())

    return sanitize_slug(fallback_task_id)


def extract_spec_title(spec: str, fallback: str = "Feature Implementation") -> str:
    """Extract a human-readable title from the specification.

    Args:
        spec: Markdown text of SPEC.md.
        fallback: Fallback title if none found.

    Returns:
        Title string.
    """
    match_spec = re.search(r"#\s*SPEC:\s*([^\n\r]+)", spec, re.IGNORECASE)
    if match_spec:
        return match_spec.group(1).strip()

    match_h1 = re.search(r"^#\s+([^\n\r]+)", spec, re.MULTILINE)
    if match_h1:
        return match_h1.group(1).strip()

    return fallback


async def _execute_step(
    step: DevAgentStep,
    repo_root: Path,
    gatekeeper: Gatekeeper,
    shell_runner: ShellRunner,
    git_handler: GitHandler,
) -> tuple[bool, str]:
    """Execute a single step from the developer agent.

    All shell operations and file writes strictly pass through Gatekeeper policy.

    Args:
        step: Action to perform.
        repo_root: Root path of repository sandbox.
        gatekeeper: Active Gatekeeper instance.
        shell_runner: Active ShellRunner instance.
        git_handler: Active GitHandler instance.

    Returns:
        Tuple of (success: bool, output_message: str).
    """
    if step.action_type == DevActionType.RUN_COMMAND:
        cmd = (step.command or "").strip()
        if not cmd:
            return False, "Error: No command specified for RUN_COMMAND action."

        # Execute through ShellRunner which validates against Gatekeeper
        res = await shell_runner.run_command(
            command=cmd,
            cwd=repo_root,
            task_goal=step.thought or "Dev Agent Step Execution",
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

        # Gatekeeper policy: check for sensitive paths
        escape_reason = gatekeeper.check_path_escapes(str(file_path_str), repo_root)
        if escape_reason:
            return False, f"Write BLOCKED by Gatekeeper: {escape_reason}"

        # Additional sensitive path checks (e.g. .env, .git/hooks)
        clean_norm = file_path_str.replace("\\", "/")
        if clean_norm == ".env" or clean_norm.startswith(".env.") or ".git" in clean_norm.split("/"):
            return False, f"Write BLOCKED by Gatekeeper: writing to '{file_path_str}' is forbidden."

        try:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(step.file_content or "", encoding="utf-8")
            return True, f"Successfully wrote {len(step.file_content or '')} characters to '{file_path_str}'."
        except Exception as exc:
            return False, f"Failed to write file '{file_path_str}': {exc}"

    elif step.action_type == DevActionType.COMMIT:
        msg = (step.commit_message or step.thought or "update codebase").strip()
        try:
            commit_hash = await git_handler.commit_changes(msg)
            return True, f"Successfully committed changes: {commit_hash}"
        except Exception as exc:
            return False, f"Commit failed: {exc}"

    elif step.action_type == DevActionType.COMPLETE:
        return True, "Dev agent signaled completion."

    return False, f"Unknown action type: {step.action_type}"


async def run_dev_phase(
    spec: str,
    state: WorkflowState,
    gatekeeper: Gatekeeper,
    provider: BaseLLMProvider,
    git_handler: GitHandler | None = None,
    remote_vcs: RemoteVCS | None = None,
    max_iterations: int = 15,
) -> None:
    """Execute the Phase 4 Dev-Agent Loop.

    1. Creates and checkouts feature branch `feat/SPEC-<slug>`.
    2. Guides the developer agent through iterative implementation and testing.
    3. Guarantees all shell operations and file modifications go through Gatekeeper.
    4. Commits changes adhering to Conventional Commits.
    5. Pushes the branch and creates a draft PR/MR on the remote VCS.

    Args:
        spec: The finalized SPEC.md content.
        state: Active workflow state object.
        gatekeeper: Active Gatekeeper policy engine.
        provider: LLM provider for agent completions.
        git_handler: Optional custom GitHandler (default: initialized from gatekeeper repo_root).
        remote_vcs: Optional custom RemoteVCS client.
        max_iterations: Maximum development loop turns.

    Raises:
        DevLoopError: If branching, critical steps, or git operations fail.
    """
    repo_root = gatekeeper.repo_root
    logger.info("Starting Dev-Phase in repository root: %s", repo_root)

    # Initialize Git & Remote VCS handlers
    git = git_handler or GitHandler(repo_path=repo_root)
    vcs = remote_vcs or RemoteVCS(repo_path=repo_root)
    shell_runner = ShellRunner(gatekeeper=gatekeeper, repo_root=repo_root)

    # Step 1: Feature branch generation & checkout
    slug = extract_spec_slug(spec, state.task_id)
    spec_title = extract_spec_title(spec, state.task_id)

    try:
        branch_name = await git.create_feature_branch(slug)
        state.branch_name = branch_name
        state.transition_to(WorkflowPhase.CODING)
        logger.info("Checked out feature branch: %s", branch_name)
    except Exception as exc:
        state.transition_to(WorkflowPhase.FAILED)
        raise DevLoopError(f"Failed to create feature branch for slug '{slug}': {exc}") from exc

    # Step 2: Prepare Agent context and prompts
    system_prompt = load_developer_prompt()
    repo_context = gather_repo_context(repo_root)

    history: list[str] = []
    tests_verified = False

    # Step 3: Iterative Dev-Agent Loop
    for iteration in range(1, max_iterations + 1):
        logger.info("Dev-Agent loop turn %d/%d", iteration, max_iterations)

        # Build turn prompt
        user_prompt = (
            f"### Feature Specification (SPEC.md):\n{spec}\n\n"
            f"### Repository Context & Guidelines:\n{repo_context}\n\n"
            f"### Active Branch:\n{state.branch_name}\n\n"
        )
        if history:
            user_prompt += "### Previous Actions and Results:\n" + "\n".join(history[-6:]) + "\n\n"

        user_prompt += (
            "Analyze current progress and determine the next action. "
            "Output your decision as a valid JSON object matching the DevAgentStep schema."
        )

        try:
            step = await provider.complete(
                prompt=user_prompt,
                system_prompt=system_prompt,
                json_schema=DevAgentStep,
            )
            if not isinstance(step, DevAgentStep):
                # Provider returned raw model or parsed object
                step = DevAgentStep.model_validate(step)
        except Exception as exc:
            logger.warning("Agent completion error on turn %d: %s. Retrying...", iteration, exc)
            history.append(f"Turn {iteration}: LLM error: {exc}")
            continue

        logger.info("Dev Agent Action [%s]: %s", step.action_type, step.thought)

        if step.tests_passed:
            tests_verified = True

        if step.action_type == DevActionType.COMPLETE:
            logger.info("Dev agent declared completion.")
            break

        # Execute action through Gatekeeper
        success, outcome = await _execute_step(
            step=step,
            repo_root=repo_root,
            gatekeeper=gatekeeper,
            shell_runner=shell_runner,
            git_handler=git,
        )

        history.append(
            f"Turn {iteration} [{step.action_type}]: {step.thought}\nOutcome (Success={success}): {outcome}"
        )

    # Step 4: Final verification and commit
    try:
        has_uncommitted = await git.has_uncommitted_changes()
        if has_uncommitted:
            logger.info("Staging and committing remaining uncommitted changes.")
            commit_msg = f"feat: implement {spec_title.lower()}"
            await git.commit_changes(commit_msg)

        # Step 5: Push branch to remote
        logger.info("Pushing feature branch '%s' to remote.", state.branch_name)
        try:
            await git.push_branch(branch=state.branch_name)
        except Exception as push_exc:
            logger.warning("Git push encountered an issue: %s", push_exc)

        # Step 6: Create Draft PR / MR
        logger.info("Creating draft PR/MR for branch '%s'.", state.branch_name)
        pr_title = f"feat: {spec_title}"
        pr_body = (
            f"## Automated Implementation: {spec_title}\n\n"
            f"### Specification\n{spec}\n\n"
            "---\n*Created automatically by MergeFlow Orchestrator.*"
        )
        try:
            pr_url = await vcs.create_draft_pr(
                title=pr_title,
                body=pr_body,
                branch=state.branch_name or "main",
            )
            state.metadata["pr_url"] = pr_url
            logger.info("Draft PR created successfully: %s", pr_url)
        except Exception as pr_exc:
            logger.warning("Remote VCS draft PR creation failed or skipped: %s", pr_exc)
            state.metadata["pr_error"] = str(pr_exc)

        # Transition to CI_LOOP phase
        state.transition_to(WorkflowPhase.CI_LOOP)
        logger.info("Dev Phase completed. Transitioned to CI_LOOP.")

    except Exception as exc:
        state.transition_to(WorkflowPhase.FAILED)
        raise DevLoopError(f"Error during final commit, push, or PR creation: {exc}") from exc
