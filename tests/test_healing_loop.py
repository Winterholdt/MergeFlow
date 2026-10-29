"""Comprehensive tests for Self-Healing CI Loop controller (Phase 5)."""

import asyncio
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.dev_loop import DevActionType, DevAgentStep
from core.healing_loop import (
    CIHealingError,
    extract_pr_number_from_url,
    format_ci_commit_message,
    load_ci_healing_prompt,
    run_ci_healing_loop,
)
from core.models import FindingSeverity, PipelineState, WorkflowPhase, WorkflowState
from core.provider import BaseLLMProvider
from security.gatekeeper import Gatekeeper
from vcs.ci_watcher import CIWatcher, PipelineResult
from vcs.git_handler import GitHandler
from vcs.remote_vcs import RemoteVCS, VCSType


@pytest.fixture
def repo_sandbox(tmp_path: Path) -> Path:
    """Creates a temporary git repository configured for healing loop testing."""
    repo = tmp_path / "sandbox_repo"
    repo.mkdir()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "HealingAgent Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "healing@mergeflow.ai"], cwd=repo, check=True, capture_output=True)

    (repo / "README.md").write_text("# Sandbox Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo, check=True, capture_output=True)

    return repo


# ===========================================================================
# 1. HELPER & PROMPT LOADING TESTS
# ===========================================================================

def test_load_ci_healing_prompt():
    prompt = load_ci_healing_prompt()
    assert "Autonomous CI Healing Agent" in prompt
    assert "fix(ci):" in prompt
    assert "Gatekeeper" in prompt


def test_load_ci_healing_prompt_fallback():
    prompt = load_ci_healing_prompt("nonexistent/path/to/prompt.md")
    assert "Autonomous CI Healing Agent" in prompt
    assert "fix(ci):" in prompt


def test_format_ci_commit_message():
    assert format_ci_commit_message("fix tests") == "fix(ci): fix tests"
    assert format_ci_commit_message("fix: typo in calc") == "fix(ci): typo in calc"
    assert format_ci_commit_message("fix(ci): correct auth model") == "fix(ci): correct auth model"
    assert format_ci_commit_message("") == "fix(ci): resolve pipeline failure"
    assert format_ci_commit_message(None) == "fix(ci): resolve pipeline failure"


def test_extract_pr_number_from_url():
    assert extract_pr_number_from_url("https://github.com/org/repo/pull/42") == 42
    assert extract_pr_number_from_url("https://gitlab.com/org/repo/-/merge_requests/99") == 99
    assert extract_pr_number_from_url("https://github.com/org/repo") is None
    assert extract_pr_number_from_url(None) is None


# ===========================================================================
# 2. RUN CI HEALING LOOP: IMMEDIATE SUCCESS
# ===========================================================================

@pytest.mark.asyncio
async def test_run_ci_healing_loop_immediate_success(repo_sandbox: Path):
    state = WorkflowState(
        task_id="task-success",
        raw_requirements="Implement feature",
        branch_name="feat/SPEC-success",
    )
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    git = GitHandler(repo_path=repo_sandbox)

    # Mock CIWatcher returning success on first call
    mock_watcher = AsyncMock(spec=CIWatcher)
    mock_watcher.wait_for_pipeline.return_value = PipelineResult(
        status=PipelineState.SUCCESS,
        passed=True,
        error_logs="",
        raw_logs="All jobs passed.",
    )

    success = await run_ci_healing_loop(
        state=state,
        gatekeeper=gatekeeper,
        git_handler=git,
        ci_watcher=mock_watcher,
    )

    assert success is True
    assert state.current_phase == WorkflowPhase.REVIEW.value
    assert state.ci_retry_count == 0
    assert mock_watcher.wait_for_pipeline.call_count == 1


# ===========================================================================
# 3. RUN CI HEALING LOOP: HEALING AGENT RESOLVES FAILURE
# ===========================================================================

@pytest.mark.asyncio
async def test_run_ci_healing_loop_agent_remediation_success(repo_sandbox: Path):
    # Setup a python file in the repo with a bug
    buggy_file = repo_sandbox / "calc.py"
    buggy_file.write_text("def add(a, b):\n    return a - b  # bug\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_sandbox, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: implement calc"], cwd=repo_sandbox, check=True, capture_output=True)

    state = WorkflowState(
        task_id="task-calc",
        raw_requirements="Fix calculator",
        branch_name="feat/SPEC-calc",
    )
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    git = GitHandler(repo_path=repo_sandbox)

    # Mock CIWatcher: 1st call FAILED, 2nd call SUCCESS
    mock_watcher = AsyncMock(spec=CIWatcher)
    mock_watcher.wait_for_pipeline.side_effect = [
        PipelineResult(
            status=PipelineState.FAILED,
            passed=False,
            error_logs="AssertionError: assert add(2, 2) == 4, got 0",
            raw_logs="Raw failure logs",
        ),
        PipelineResult(
            status=PipelineState.SUCCESS,
            passed=True,
            error_logs="",
            raw_logs="All tests passed.",
        ),
    ]

    # Mock Git push to avoid actual remote network call
    git.push_branch = AsyncMock(return_value="Everything up-to-date")

    # Mock LLM provider emitting healing actions
    mock_provider = AsyncMock(spec=BaseLLMProvider)
    mock_provider.complete.side_effect = [
        # Turn 1: Write fixed code
        DevAgentStep(
            thought="Fix the subtraction bug in calc.py to addition.",
            action_type=DevActionType.WRITE_FILE,
            file_path="calc.py",
            file_content="def add(a, b):\n    return a + b\n",
        ),
        # Turn 2: Commit fix
        DevAgentStep(
            thought="Commit the fix with Conventional Commit.",
            action_type=DevActionType.COMMIT,
            commit_message="fix(ci): correct addition operator in calc.py",
        ),
        # Turn 3: Complete
        DevAgentStep(
            thought="Remediation complete.",
            action_type=DevActionType.COMPLETE,
            tests_passed=True,
        ),
    ]

    success = await run_ci_healing_loop(
        state=state,
        gatekeeper=gatekeeper,
        provider=mock_provider,
        git_handler=git,
        ci_watcher=mock_watcher,
        max_retries=3,
    )

    assert success is True
    assert state.current_phase == WorkflowPhase.REVIEW.value
    assert state.ci_retry_count == 1
    assert mock_watcher.wait_for_pipeline.call_count == 2
    git.push_branch.assert_called_once()

    # Verify file was actually fixed
    assert buggy_file.read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


# ===========================================================================
# 4. RUN CI HEALING LOOP: CIRCUIT BREAKER LIMIT EXCEEDED
# ===========================================================================

@pytest.mark.asyncio
async def test_run_ci_healing_loop_circuit_breaker(repo_sandbox: Path):
    state = WorkflowState(
        task_id="task-circuit-breaker",
        raw_requirements="Implement feature",
        branch_name="feat/SPEC-cb",
    )
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    git = GitHandler(repo_path=repo_sandbox)
    git.push_branch = AsyncMock(return_value="Everything up-to-date")

    # Watcher consistently fails
    mock_watcher = AsyncMock(spec=CIWatcher)
    mock_watcher.wait_for_pipeline.return_value = PipelineResult(
        status=PipelineState.FAILED,
        passed=False,
        error_logs="Persistent pipeline failure",
    )

    # Provider emits simple complete action on each retry turn
    mock_provider = AsyncMock(spec=BaseLLMProvider)
    mock_provider.complete.return_value = DevAgentStep(
        thought="Attempting fix.",
        action_type=DevActionType.COMPLETE,
    )

    # Max retries = 2
    success = await run_ci_healing_loop(
        state=state,
        max_retries=2,
        gatekeeper=gatekeeper,
        provider=mock_provider,
        git_handler=git,
        ci_watcher=mock_watcher,
    )

    assert success is False
    assert state.current_phase == WorkflowPhase.FAILED.value
    assert state.ci_retry_count == 2
    assert state.metadata.get("circuit_breaker_triggered") is True

    # Verify finding was registered for user notification
    blocker_findings = [f for f in state.findings if f.severity == FindingSeverity.BLOCKER]
    assert len(blocker_findings) == 1
    assert "Circuit breaker" in blocker_findings[0].issue


# ===========================================================================
# 5. GATEKEEPER SECURITY ENFORCEMENT IN HEALING STEP
# ===========================================================================

@pytest.mark.asyncio
async def test_healing_step_security_gatekeeper_blocks_escapes(repo_sandbox: Path):
    from core.healing_loop import _execute_healing_step
    from security.shell_runner import ShellRunner

    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    shell_runner = ShellRunner(gatekeeper=gatekeeper, repo_root=repo_sandbox)
    git = GitHandler(repo_path=repo_sandbox)

    # 1. Path traversal escape attempt
    escape_step = DevAgentStep(
        thought="Try to write outside repo",
        action_type=DevActionType.WRITE_FILE,
        file_path="../../malicious.py",
        file_content="evil()",
    )
    success, msg = await _execute_healing_step(escape_step, repo_sandbox, gatekeeper, shell_runner, git)
    assert success is False
    assert "BLOCKED by Gatekeeper" in msg

    # 2. Writing to sensitive path (.env)
    sensitive_step = DevAgentStep(
        thought="Try to overwrite .env",
        action_type=DevActionType.WRITE_FILE,
        file_path=".env",
        file_content="SECRET=leaked",
    )
    success, msg = await _execute_healing_step(sensitive_step, repo_sandbox, gatekeeper, shell_runner, git)
    assert success is False
    assert "BLOCKED by Gatekeeper" in msg

    # 3. Blocked command
    blocked_cmd = DevAgentStep(
        thought="Try to run destructive command",
        action_type=DevActionType.RUN_COMMAND,
        command="rm -rf /",
    )
    success, msg = await _execute_healing_step(blocked_cmd, repo_sandbox, gatekeeper, shell_runner, git)
    assert success is False
    assert "BLOCKED by Gatekeeper" in msg


# ===========================================================================
# 6. MISSING PROVIDER RAISES ERROR WHEN HEALING NEEDED
# ===========================================================================

@pytest.mark.asyncio
async def test_run_ci_healing_loop_missing_provider_raises(repo_sandbox: Path):
    state = WorkflowState(
        task_id="task-no-provider",
        raw_requirements="Implement feature",
    )
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    git = GitHandler(repo_path=repo_sandbox)

    mock_watcher = AsyncMock(spec=CIWatcher)
    mock_watcher.wait_for_pipeline.return_value = PipelineResult(
        status=PipelineState.FAILED,
        passed=False,
        error_logs="Pipeline failure",
    )

    with pytest.raises(CIHealingError) as exc_info:
        await run_ci_healing_loop(
            state=state,
            provider=None,
            gatekeeper=gatekeeper,
            git_handler=git,
            ci_watcher=mock_watcher,
        )

    assert "No BaseLLMProvider was supplied" in str(exc_info.value)
