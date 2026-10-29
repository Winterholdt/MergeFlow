"""Unit and integration tests for Orchestrator resilience, hardening, and crash-recovery."""

import asyncio
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.config import AppConfig, load_config
from core.consensus import (
    ConsensusDecision,
    ConsensusStatus,
    run_consensus_phase,
)
from core.dev_loop import DevActionType, DevAgentStep, run_dev_phase
from core.healing_loop import run_ci_healing_loop
from core.models import (
    Finding,
    PipelineState,
    ReviewResult,
    ReviewSummary,
    Severity,
    WorkflowPhase,
    WorkflowState,
)
from core.orchestrator import Orchestrator, OrchestratorError, run_orchestrator
from core.provider import BaseLLMProvider
from core.reviewer import _build_review_user_prompt
from core.state_machine import (
    DEFAULT_STATE_FILE,
    determine_resume_phase,
    load_state,
    save_atomic_state,
)
from main import async_main, build_parser
from security.gatekeeper import Gatekeeper
from security.shell_runner import ShellRunner, run_command
from vcs.ci_watcher import CIWatcher, PipelineResult
from vcs.git_handler import (
    DEFAULT_MAX_DIFF_LINES,
    DEFAULT_MAX_DIFF_WORDS,
    GitHandler,
    truncate_or_split_diff,
)
from vcs.remote_vcs import RemoteVCS, VCSType


@pytest.fixture
def repo_sandbox(tmp_path: Path) -> Path:
    """Creates a temporary git repository configured for testing."""
    repo = tmp_path / "sandbox_repo"
    repo.mkdir()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Resilience Tester"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "resilience@mergeflow.ai"], cwd=repo, check=True, capture_output=True)

    (repo / "README.md").write_text("# Sandbox Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo, check=True, capture_output=True)

    return repo


# =============================================================================
# 1. Crash-Resilience & State-Persistenz Tests
# =============================================================================


def test_atomic_state_persistence_and_load(tmp_path: Path):
    """WorkflowState is saved atomically and can be loaded accurately."""
    state_file = tmp_path / ".orchestrator" / "state.json"

    state = WorkflowState(
        task_id="TASK-RESILIENCE-01",
        raw_requirements="Resilience test task",
        current_phase=WorkflowPhase.CI_LOOP.value,
        ci_retry_count=1,
        review_retry_count=1,
        spec_markdown="# SPEC: Resilience\nCriteria",
    )

    saved_path = save_atomic_state(state, state_file)
    assert saved_path.is_file()

    loaded = load_state(state_file, task_id="TASK-RESILIENCE-01")
    assert loaded.task_id == "TASK-RESILIENCE-01"
    assert loaded.current_phase == WorkflowPhase.CI_LOOP.value
    assert loaded.spec_markdown == "# SPEC: Resilience\nCriteria"

    # Mismatched task_id raises ValueError
    with pytest.raises(ValueError, match="does not match requested task_id"):
        load_state(state_file, task_id="TASK-OTHER")


def test_determine_resume_phase():
    """Correctly identifies the phase to resume execution from."""
    # Active in CI_LOOP
    s1 = WorkflowState(task_id="t1", raw_requirements="r", current_phase=WorkflowPhase.CI_LOOP.value)
    assert determine_resume_phase(s1) == WorkflowPhase.CI_LOOP

    # Active in REVIEW
    s2 = WorkflowState(task_id="t2", raw_requirements="r", current_phase=WorkflowPhase.REVIEW.value)
    assert determine_resume_phase(s2) == WorkflowPhase.REVIEW

    # Stopped in HUMAN_INTERVENTION_REQUIRED with recorded failed_phase
    s3 = WorkflowState(
        task_id="t3",
        raw_requirements="r",
        current_phase=WorkflowPhase.HUMAN_INTERVENTION_REQUIRED.value,
        metadata={"failed_phase": WorkflowPhase.REVIEW.value},
    )
    assert determine_resume_phase(s3) == WorkflowPhase.REVIEW

    # Already completed
    s4 = WorkflowState(task_id="t4", raw_requirements="r", current_phase=WorkflowPhase.DONE.value)
    assert determine_resume_phase(s4) == WorkflowPhase.DONE


@pytest.mark.asyncio
async def test_resume_skips_completed_phases(repo_sandbox: Path, tmp_path: Path):
    """Resuming from CI_LOOP skips Phase 3 (Discovery) and Phase 4 (Dev Loop)."""
    state_file = tmp_path / "resume_state.json"

    # Pre-populate state in CI_LOOP
    saved_state = WorkflowState(
        task_id="TASK-RESUME-02",
        raw_requirements="Implement caching",
        spec_markdown="# SPEC: Caching\nAcceptance criteria",
        branch_name="feat/SPEC-caching",
        current_phase=WorkflowPhase.CI_LOOP.value,
    )
    saved_state.save_to_file(state_file)

    mock_provider = AsyncMock(spec=BaseLLMProvider)
    remote_vcs = RemoteVCS(repo_sandbox, runner=lambda b, *args: (0, "merged\n", ""))
    git = GitHandler(repo_sandbox)
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)

    mock_watcher = AsyncMock(spec=CIWatcher)
    mock_watcher.wait_for_pipeline.return_value = PipelineResult(
        status=PipelineState.SUCCESS,
        passed=True,
    )

    clean_summary = ReviewSummary(
        all_approved=True,
        has_blockers=False,
        is_approved=True,
        reviews=[ReviewResult(reviewer="functional", status="APPROVED", summary="OK")],
    )

    orchestrator = Orchestrator(
        provider=mock_provider,
        gatekeeper=gatekeeper,
        git_handler=git,
        remote_vcs=remote_vcs,
        ci_watcher=mock_watcher,
        repo_path=repo_sandbox,
        state_path=state_file,
        auto_merge=True,
    )

    discovery_called = False
    dev_called = False

    async def mock_discovery(*args, **kwargs):
        nonlocal discovery_called
        discovery_called = True
        return "# SPEC"

    async def mock_dev(*args, **kwargs):
        nonlocal dev_called
        dev_called = True

    with (
        patch("core.orchestrator.run_discovery_phase", side_effect=mock_discovery),
        patch("core.orchestrator.run_dev_phase", side_effect=mock_dev),
        patch("core.orchestrator.run_review_phase", new_callable=AsyncMock) as mock_review,
    ):
        mock_review.return_value = clean_summary

        final_state = await orchestrator.run(
            requirements="Implement caching",
            resume_from_state=True,
        )

    # Verification: Phase 3 and Phase 4 were skipped!
    assert not discovery_called
    assert not dev_called
    assert final_state.current_phase == WorkflowPhase.DONE.value


@pytest.mark.asyncio
async def test_main_cli_resume_flag(tmp_path: Path):
    """CLI --resume correctly restores state and continues."""
    state_file = tmp_path / ".orchestrator" / "state.json"
    pre_state = WorkflowState(
        task_id="TASK-CLI-RESUME",
        raw_requirements="Add metrics endpoint",
        spec_markdown="# SPEC: Metrics",
        current_phase=WorkflowPhase.CI_LOOP.value,
    )
    pre_state.save_to_file(state_file)

    parser = build_parser()
    args = parser.parse_args(["--resume", "--state", str(state_file)])
    assert args.resume == "LAST"
    assert args.state_path == str(state_file)

    completed_state = WorkflowState(
        task_id="TASK-CLI-RESUME",
        raw_requirements="Add metrics endpoint",
        current_phase=WorkflowPhase.DONE.value,
    )

    with patch("main.run_orchestrator", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = completed_state
        exit_code = await async_main(args)

    assert exit_code == 0
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["resume_from_state"] is True
    assert mock_run.call_args.kwargs["task_id"] == "TASK-CLI-RESUME"


# =============================================================================
# 2. Circuit-Breaker & Ping-Pong-Bremse Tests
# =============================================================================


@pytest.mark.asyncio
async def test_ci_healing_circuit_breaker_manual_takeover(repo_sandbox: Path):
    """When CI retries reach limit, circuit breaker trips and allows manual takeover [M]."""
    state = WorkflowState(
        task_id="task-ci-cb",
        raw_requirements="Fix auth",
        branch_name="feat/SPEC-cb-ci",
        ci_retry_count=3,  # Already at max 3
    )
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    git = GitHandler(repo_sandbox)

    mock_watcher = AsyncMock(spec=CIWatcher)
    mock_watcher.wait_for_pipeline.return_value = PipelineResult(
        status=PipelineState.FAILED,
        passed=False,
        error_logs="Compilation failed on line 42",
    )

    # User chooses 'M' (Manual takeover)
    res = await run_ci_healing_loop(
        state=state,
        max_retries=3,
        gatekeeper=gatekeeper,
        git_handler=git,
        ci_watcher=mock_watcher,
        input_func=lambda prompt: "m",
    )

    assert res is False
    assert state.current_phase == WorkflowPhase.HUMAN_INTERVENTION_REQUIRED.value
    assert state.metadata.get("circuit_breaker_triggered") is True
    assert state.metadata.get("failed_phase") == WorkflowPhase.CI_LOOP.value


@pytest.mark.asyncio
async def test_ci_healing_circuit_breaker_force_merge(repo_sandbox: Path):
    """When CI retries reach limit, user can choose Force Merge [F]."""
    state = WorkflowState(
        task_id="task-ci-force",
        raw_requirements="Fix auth",
        branch_name="feat/SPEC-cb-ci-force",
        ci_retry_count=3,
        metadata={"pr_number": 10},
    )
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    git = GitHandler(repo_sandbox)
    vcs = RemoteVCS(repo_sandbox, runner=lambda b, *args: (0, "PR 10 merged\n", ""))

    mock_watcher = AsyncMock(spec=CIWatcher)
    mock_watcher.wait_for_pipeline.return_value = PipelineResult(
        status=PipelineState.FAILED,
        passed=False,
        error_logs="Flaky test failure",
    )

    res = await run_ci_healing_loop(
        state=state,
        max_retries=3,
        gatekeeper=gatekeeper,
        git_handler=git,
        remote_vcs=vcs,
        ci_watcher=mock_watcher,
        input_func=lambda prompt: "f",
    )

    assert res is True
    assert state.current_phase == WorkflowPhase.DONE.value
    assert state.metadata["merged"] is True
    assert state.metadata["merge_mode"] == "force"


@pytest.mark.asyncio
async def test_ci_healing_circuit_breaker_abort(repo_sandbox: Path):
    """When CI retries reach limit, user can abort [A]."""
    state = WorkflowState(
        task_id="task-ci-abort",
        raw_requirements="Fix auth",
        branch_name="feat/SPEC-cb-ci-abort",
        ci_retry_count=3,
    )
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    git = GitHandler(repo_sandbox)

    mock_watcher = AsyncMock(spec=CIWatcher)
    mock_watcher.wait_for_pipeline.return_value = PipelineResult(
        status=PipelineState.FAILED,
        passed=False,
        error_logs="Fatal error",
    )

    res = await run_ci_healing_loop(
        state=state,
        max_retries=3,
        gatekeeper=gatekeeper,
        git_handler=git,
        ci_watcher=mock_watcher,
        input_func=lambda prompt: "a",
    )

    assert res is False
    assert state.current_phase == WorkflowPhase.FAILED.value


@pytest.mark.asyncio
async def test_review_consensus_circuit_breaker_manual_takeover(repo_sandbox: Path):
    """When review cycles reach limit (e.g. 2), circuit breaker offers [M]."""
    state = WorkflowState(
        task_id="task-review-cb",
        raw_requirements="Refactor payment",
        review_retry_count=2,  # Reached max 2 cycles
    )

    f_block = Finding(file="pay.py", line=10, severity=Severity.BLOCKER, issue="Unsafe SQL query")
    review_with_blocker = ReviewSummary(
        all_approved=False,
        has_blockers=True,
        is_approved=False,
        blockers=[f_block],
        reviews=[ReviewResult(reviewer="security", status="CHANGES_REQUESTED", summary="SQL injection risk", findings=[f_block])],
    )

    vcs = RemoteVCS(repo_sandbox)

    decision = await run_consensus_phase(
        summary=review_with_blocker,
        state=state,
        remote_vcs=vcs,
        max_retries=2,
        input_func=lambda prompt: "m",
    )

    assert decision.approved is False
    assert decision.status == ConsensusStatus.REJECTED
    assert state.current_phase == WorkflowPhase.HUMAN_INTERVENTION_REQUIRED.value
    assert state.metadata.get("review_circuit_breaker") is True


@pytest.mark.asyncio
async def test_review_consensus_circuit_breaker_force_merge(repo_sandbox: Path):
    """When review cycles reach limit, user can choose Force Merge [F]."""
    state = WorkflowState(
        task_id="task-review-force",
        raw_requirements="Emergency patch",
        review_retry_count=2,
        metadata={"pr_number": 99},
    )

    f_warn = Finding(file="log.py", severity=Severity.WARNING, issue="Missing format string")
    summary = ReviewSummary(
        all_approved=False,
        has_blockers=False,
        is_approved=False,
        warnings=[f_warn],
        reviews=[ReviewResult(reviewer="qa_testing", status="CHANGES_REQUESTED", summary="Warning only", findings=[f_warn])],
    )

    vcs = RemoteVCS(repo_sandbox, runner=lambda b, *args: (0, "PR 99 force-merged\n", ""))

    decision = await run_consensus_phase(
        summary=summary,
        state=state,
        remote_vcs=vcs,
        max_retries=2,
        input_func=lambda prompt: "f",
    )

    assert decision.approved is True
    assert decision.merged is True
    assert decision.merge_mode == "force"
    assert state.current_phase == WorkflowPhase.DONE.value


# =============================================================================
# 3. Scope-Drift-Guard Tests
# =============================================================================


@pytest.mark.asyncio
async def test_scope_drift_guard_detection(repo_sandbox: Path):
    """GitHandler detects deleted files and excess changes as potential scope drift."""
    git = GitHandler(repo_sandbox)
    await git.create_feature_branch("scope-check")

    # 1. Normal single file addition - no drift
    (repo_sandbox / "patch.py").write_text("print('hello')\n", encoding="utf-8")
    is_drift, reason = await git.check_scope_drift()
    assert is_drift is False

    # 2. Delete a file - drift suspected
    (repo_sandbox / "README.md").unlink()
    is_drift, reason = await git.check_scope_drift()
    assert is_drift is True
    assert "gelöscht" in reason


# =============================================================================
# 4. Deadlock-Sicherheit & ShellRunner Tests
# =============================================================================


@pytest.mark.asyncio
async def test_shell_runner_non_interactive_env_and_timeout(repo_sandbox: Path):
    """ShellRunner enforces noninteractive CI env vars and kills process on timeout."""
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    runner = ShellRunner(gatekeeper=gatekeeper, repo_root=repo_sandbox)
    state = WorkflowState(task_id="timeout-task", raw_requirements="test")

    # Command that prints env vars
    cmd_env = "python -c \"import os; print(os.environ.get('CI'), os.environ.get('DEBIAN_FRONTEND'), os.environ.get('GIT_TERMINAL_PROMPT'))\""
    res_env = await runner.run_command(cmd_env, cwd=repo_sandbox)
    assert res_env.exit_code == 0
    assert "true noninteractive 0" in res_env.stdout

    # Command that hangs / sleeps beyond timeout
    cmd_sleep = "python -c \"import time; time.sleep(10)\""
    res_timeout = await runner.run_command(cmd_sleep, cwd=repo_sandbox, timeout_seconds=1, state=state)
    assert res_timeout.exit_code == 124
    assert "timed out" in res_timeout.stderr
    # Error recorded in state
    assert len(state.findings) == 1
    assert "timed out" in state.findings[0].issue


# =============================================================================
# 5. Token-Schutz bei großen Repos Tests
# =============================================================================


def test_large_diff_splitting_and_token_protection():
    """Diffs exceeding line or word limit are dateibasiert split and annotated."""
    small_diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n+print('hi')\n"
    res, truncated = truncate_or_split_diff(small_diff, max_lines=100, max_words=100)
    assert truncated is False
    assert res == small_diff

    # Construct large diff over 100 lines across 3 files
    lines_core = ["diff --git a/core.py b/core.py\n--- a/core.py\n+++ b/core.py\n@@ -1,5 +1,5 @@\n"] + [f"+line {i}\n" for i in range(70)]
    lines_lock = ["diff --git a/package-lock.json b/package-lock.json\n--- a/package-lock.json\n+++ b/package-lock.json\n@@ -1,5 +1,5 @@\n"] + [f"+lock line {i}\n" for i in range(80)]
    large_diff = "".join(lines_core) + "".join(lines_lock)

    stat = " core.py | 70 ++\n package-lock.json | 80 ++\n 2 files changed, 150 insertions(+)"

    res_large, truncated_large = truncate_or_split_diff(
        large_diff,
        stat=stat,
        max_lines=90,
        max_words=500,
    )

    assert truncated_large is True
    assert "HINWEIS: Der Diff hat das Token-Limit überschritten" in res_large
    assert "core.py" in res_large
    assert "### Git Diff --stat" in res_large

    # Test reviewer user prompt includes notice
    prompt = _build_review_user_prompt(diff=res_large, spec="Test Spec")
    assert "WICHTIGER HINWEIS FÜR REVIEWER" in prompt
