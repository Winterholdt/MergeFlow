"""Unit and integration tests for the Central Orchestrator (Phase 1-7)."""

from pathlib import Path
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.config import AppConfig, WorkflowConfig
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
from security.gatekeeper import Gatekeeper
from vcs.ci_watcher import CIWatcher, PipelineResult
from vcs.git_handler import GitHandler
from vcs.remote_vcs import RemoteVCS, VCSType


@pytest.fixture
def repo_sandbox(tmp_path: Path) -> Path:
    """Creates a temporary git repository configured for orchestrator testing."""
    repo = tmp_path / "sandbox_repo"
    repo.mkdir()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Orchestrator Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "orch@mergeflow.ai"], cwd=repo, check=True, capture_output=True)

    (repo / "README.md").write_text("# Sandbox Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo, check=True, capture_output=True)

    return repo


def test_state_persistence_roundtrip(tmp_path: Path):
    """WorkflowState can be serialized and restored from disk without data loss."""
    state_file = tmp_path / "state.json"
    state = WorkflowState(
        task_id="TASK-42",
        raw_requirements="Implement feature X",
        current_phase=WorkflowPhase.CODING.value,
        ci_retry_count=1,
        review_retry_count=2,
        spec_markdown="# SPEC\nDetails",
    )
    state.add_finding(Finding(file="a.py", severity=Severity.WARNING, issue="Issue 1"))

    saved_path = state.save_to_file(state_file)
    assert saved_path.is_file()

    loaded = WorkflowState.load_from_file(state_file)
    assert loaded.task_id == "TASK-42"
    assert loaded.current_phase == "CODING"
    assert loaded.ci_retry_count == 1
    assert loaded.review_retry_count == 2
    assert loaded.spec_markdown == "# SPEC\nDetails"
    assert len(loaded.findings) == 1
    assert loaded.findings[0].issue == "Issue 1"


@pytest.mark.asyncio
async def test_orchestrator_happy_path(repo_sandbox: Path, tmp_path: Path):
    """End-to-end flow: Discovery -> Dev Loop -> CI Healing -> Review -> Consensus -> Merge -> DONE."""
    state_file = tmp_path / "orch_state.json"

    # Mock provider
    mock_provider = AsyncMock(spec=BaseLLMProvider)

    # Mock remote VCS runner
    merged_calls = []

    async def mock_vcs_runner(binary, *args):
        if "merge" in args:
            merged_calls.append((binary, list(args)))
            return 0, "PR merged successfully\n", ""
        if "create" in args:
            return 0, "https://github.com/org/repo/pull/1\n", ""
        return 0, "", ""

    remote_vcs = RemoteVCS(repo_path=repo_sandbox, vcs_type=VCSType.GITHUB, runner=mock_vcs_runner)
    git = GitHandler(repo_path=repo_sandbox)
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)

    # Mock CI watcher
    mock_watcher = AsyncMock(spec=CIWatcher)
    mock_watcher.wait_for_pipeline.return_value = PipelineResult(
        status=PipelineState.SUCCESS,
        passed=True,
        raw_logs="CI Success",
    )

    # Mock Review phase
    clean_reviews = [
        ReviewResult(reviewer="functional", status="APPROVED", summary="OK"),
        ReviewResult(reviewer="architecture", status="APPROVED", summary="OK"),
        ReviewResult(reviewer="security", status="APPROVED", summary="OK"),
        ReviewResult(reviewer="qa_testing", status="APPROVED", summary="OK"),
    ]

    clean_summary = ReviewSummary(
        all_approved=True,
        has_blockers=False,
        is_approved=True,
        reviews=clean_reviews,
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

    with (
        patch("core.orchestrator.run_discovery_phase", new_callable=AsyncMock) as mock_discovery,
        patch("core.orchestrator.run_dev_phase", new_callable=AsyncMock) as mock_dev,
        patch("core.orchestrator.run_review_phase", new_callable=AsyncMock) as mock_review,
    ):
        mock_discovery.return_value = "# SPEC: Awesome Feature\nAcceptance criteria"
        mock_review.return_value = clean_summary

        final_state = await orchestrator.run("Build awesome feature")

    assert final_state.current_phase == WorkflowPhase.DONE.value
    assert final_state.spec_markdown == "# SPEC: Awesome Feature\nAcceptance criteria"
    assert final_state.metadata["merged"] is True
    assert final_state.metadata["merge_mode"] == "auto"
    assert state_file.is_file()
    assert len(merged_calls) == 1


@pytest.mark.asyncio
async def test_orchestrator_re_review_loop(repo_sandbox: Path, tmp_path: Path):
    """Reviewer findings trigger Fall A re-review loop: Dev -> CI -> Review -> Consensus -> DONE."""
    state_file = tmp_path / "rereview_state.json"

    mock_provider = AsyncMock(spec=BaseLLMProvider)
    remote_vcs = RemoteVCS(
        repo_path=repo_sandbox,
        vcs_type=VCSType.GITHUB,
        runner=lambda b, *args: (0, "PR merged\n", ""),
    )
    git = GitHandler(repo_path=repo_sandbox)
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)

    mock_watcher = AsyncMock(spec=CIWatcher)
    mock_watcher.wait_for_pipeline.return_value = PipelineResult(
        status=PipelineState.SUCCESS,
        passed=True,
    )

    # Round 1: finding present
    f_warn = Finding(file="calc.py", line=5, severity=Severity.WARNING, issue="Missing type hint")
    round1_reviews = [
        ReviewResult(reviewer="functional", status="APPROVED", summary="OK"),
        ReviewResult(reviewer="architecture", status="APPROVED", summary="Issue found", findings=[f_warn]),
        ReviewResult(reviewer="security", status="APPROVED", summary="OK"),
        ReviewResult(reviewer="qa_testing", status="APPROVED", summary="OK"),
    ]
    round1_summary = ReviewSummary(
        all_approved=True,
        has_blockers=False,
        is_approved=False,
        warnings=[f_warn],
        reviews=round1_reviews,
    )

    # Round 2: cleaned up
    round2_reviews = [
        ReviewResult(reviewer="functional", status="APPROVED", summary="OK"),
        ReviewResult(reviewer="architecture", status="APPROVED", summary="OK"),
        ReviewResult(reviewer="security", status="APPROVED", summary="OK"),
        ReviewResult(reviewer="qa_testing", status="APPROVED", summary="OK"),
    ]
    round2_summary = ReviewSummary(
        all_approved=True,
        has_blockers=False,
        is_approved=True,
        reviews=round2_reviews,
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

    dev_phase_calls = []

    async def mock_dev_phase(spec, state, gatekeeper, provider, git_handler, remote_vcs, feedback=None):
        dev_phase_calls.append(feedback)
        state.transition_to(WorkflowPhase.CI_LOOP)

    with (
        patch("core.orchestrator.run_discovery_phase", new_callable=AsyncMock) as mock_discovery,
        patch("core.orchestrator.run_dev_phase", side_effect=mock_dev_phase),
        patch("core.orchestrator.run_review_phase", side_effect=[round1_summary, round2_summary]),
    ):
        mock_discovery.return_value = "# SPEC: Calculator"

        final_state = await orchestrator.run("Build calculator")

    assert final_state.current_phase == WorkflowPhase.DONE.value
    assert final_state.review_retry_count == 1
    # Verify that dev phase was executed twice, and the 2nd time received feedback
    assert len(dev_phase_calls) == 2
    assert dev_phase_calls[0] is None
    assert "Missing type hint" in dev_phase_calls[1]


@pytest.mark.asyncio
async def test_orchestrator_halts_on_ci_failure(repo_sandbox: Path, tmp_path: Path):
    """When Phase 5 CI loop fails, orchestrator terminates with FAILED status."""
    state_file = tmp_path / "ci_fail_state.json"

    mock_provider = AsyncMock(spec=BaseLLMProvider)
    git = GitHandler(repo_path=repo_sandbox)
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)

    # CI watcher reports failure
    mock_watcher = AsyncMock(spec=CIWatcher)
    mock_watcher.wait_for_pipeline.return_value = PipelineResult(
        status=PipelineState.FAILED,
        passed=False,
        error_logs="Compilation error",
    )

    orchestrator = Orchestrator(
        provider=mock_provider,
        gatekeeper=gatekeeper,
        git_handler=git,
        ci_watcher=mock_watcher,
        repo_path=repo_sandbox,
        state_path=state_file,
    )

    with (
        patch("core.orchestrator.run_discovery_phase", new_callable=AsyncMock) as mock_discovery,
        patch("core.orchestrator.run_dev_phase", new_callable=AsyncMock) as mock_dev,
        patch("core.orchestrator.run_ci_healing_loop", return_value=False),
    ):
        mock_discovery.return_value = "# SPEC: Buggy Feature"

        final_state = await orchestrator.run("Test feature")

    assert final_state.current_phase == WorkflowPhase.FAILED.value


@pytest.mark.asyncio
async def test_run_orchestrator_initializes_provider(tmp_path: Path):
    """Verify run_orchestrator correctly instantiates provider without AttributeError."""
    state_file = tmp_path / "state.json"
    dummy_repo = tmp_path / "repo"
    dummy_repo.mkdir()

    with (
        patch("core.orchestrator.Orchestrator.run", new_callable=AsyncMock) as mock_run,
        patch("core.orchestrator.get_provider") as mock_get_provider,
    ):
        mock_provider_instance = MagicMock()
        mock_get_provider.return_value = mock_provider_instance
        mock_run.return_value = WorkflowState(task_id="TEST-1", raw_requirements="test")

        await run_orchestrator(
            requirements="Test requirements",
            repo_path=dummy_repo,
            state_path=state_file,
            provider_type="claude",
        )

        mock_get_provider.assert_called_once()
        _, kwargs = mock_get_provider.call_args
        assert kwargs["provider_type"] == "claude"
        assert isinstance(kwargs["config"], AppConfig)
