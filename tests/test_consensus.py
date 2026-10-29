"""Unit and integration tests for Phase 7 Consensus & Auto-Merge."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.config import AppConfig, WorkflowConfig
from core.consensus import (
    ConsensusDecision,
    ConsensusStatus,
    evaluate_review_consensus,
    format_feedback_json,
    format_review_feedback,
    run_consensus_phase,
)
from core.models import Finding, ReviewResult, ReviewSummary, Severity, WorkflowPhase, WorkflowState
from vcs.remote_vcs import RemoteVCS, VCSType


def make_sample_review(
    reviewer: str,
    status: str = "APPROVED",
    findings: list[Finding] | None = None,
) -> ReviewResult:
    """Helper to construct ReviewResult objects."""
    return ReviewResult(
        reviewer=reviewer,
        status=status,
        summary=f"Review summary for {reviewer}",
        findings=findings or [],
    )


# ===========================================================================
# 1. CONSENSUS EVALUATION LOGIC
# ===========================================================================

def test_evaluate_review_consensus_all_approved_clean():
    """All 4 reviews APPROVED with 0 findings evaluates to approved."""
    reviews = [
        make_sample_review("functional", "APPROVED"),
        make_sample_review("architecture", "APPROVED"),
        make_sample_review("security", "APPROVED"),
        make_sample_review("qa_testing", "APPROVED"),
    ]
    is_approved, blockers, warnings, nits, reviews_list = evaluate_review_consensus(reviews)

    assert is_approved is True
    assert len(blockers) == 0
    assert len(warnings) == 0
    assert len(nits) == 0
    assert len(reviews_list) == 4


def test_evaluate_review_consensus_approved_with_nits():
    """NIT findings do not block consensus when status is APPROVED."""
    nit = Finding(file="main.py", line=10, severity=Severity.NIT, issue="Style nit")
    reviews = [
        make_sample_review("functional", "APPROVED", [nit]),
        make_sample_review("architecture", "APPROVED"),
        make_sample_review("security", "APPROVED"),
        make_sample_review("qa_testing", "APPROVED"),
    ]
    is_approved, blockers, warnings, nits, _ = evaluate_review_consensus(reviews)

    assert is_approved is True
    assert len(blockers) == 0
    assert len(warnings) == 0
    assert len(nits) == 1


def test_evaluate_review_consensus_fails_on_warning():
    """Open WARNING findings block consensus (triggers Fall A)."""
    warn = Finding(file="auth.py", line=45, severity=Severity.WARNING, issue="Missing rate limit")
    reviews = [
        make_sample_review("functional", "APPROVED"),
        make_sample_review("architecture", "APPROVED"),
        make_sample_review("security", "APPROVED", [warn]),
        make_sample_review("qa_testing", "APPROVED"),
    ]
    is_approved, blockers, warnings, nits, _ = evaluate_review_consensus(reviews)

    assert is_approved is False
    assert len(warnings) == 1
    assert len(blockers) == 0


def test_evaluate_review_consensus_fails_on_blocker():
    """Open BLOCKER findings block consensus (triggers Fall A)."""
    blocker = Finding(file="db.py", line=12, severity=Severity.BLOCKER, issue="SQL injection vulnerability")
    reviews = [
        make_sample_review("functional", "APPROVED"),
        make_sample_review("architecture", "APPROVED"),
        make_sample_review("security", "CHANGES_REQUESTED", [blocker]),
        make_sample_review("qa_testing", "APPROVED"),
    ]
    is_approved, blockers, warnings, _, _ = evaluate_review_consensus(reviews)

    assert is_approved is False
    assert len(blockers) == 1


def test_evaluate_review_consensus_fails_on_changes_requested_without_findings():
    """If a reviewer requested changes, consensus fails even if finding list is empty."""
    reviews = [
        make_sample_review("functional", "APPROVED"),
        make_sample_review("architecture", "CHANGES_REQUESTED"),
        make_sample_review("security", "APPROVED"),
        make_sample_review("qa_testing", "APPROVED"),
    ]
    is_approved, _, _, _, _ = evaluate_review_consensus(reviews)
    assert is_approved is False


def test_evaluate_review_consensus_fails_when_fewer_than_4_reviewers():
    """Consensus requires all 4 personas to be evaluated."""
    reviews = [
        make_sample_review("functional", "APPROVED"),
        make_sample_review("architecture", "APPROVED"),
    ]
    is_approved, _, _, _, _ = evaluate_review_consensus(reviews, required_reviewers_count=4)
    assert is_approved is False


# ===========================================================================
# 2. FEEDBACK FORMATTING (MARKDOWN & JSON)
# ===========================================================================

def test_format_review_feedback_markdown_and_json():
    """Verify structured Markdown and JSON feedback formatting."""
    f_blocker = Finding(file="core/auth.py", line=42, severity=Severity.BLOCKER, issue="Secret leaked", suggestion="Use env var")
    f_warn = Finding(file="core/api.py", line=100, severity=Severity.WARNING, issue="Missing timeout", suggestion="Add timeout=30")
    f_nit = Finding(file="README.md", line=1, severity=Severity.NIT, issue="Typo", suggestion="Fix spelling")

    reviews = [
        make_sample_review("security", "CHANGES_REQUESTED", [f_blocker]),
        make_sample_review("architecture", "APPROVED", [f_warn]),
        make_sample_review("functional", "APPROVED", [f_nit]),
        make_sample_review("qa_testing", "APPROVED"),
    ]

    md_output = format_review_feedback(reviews, [f_blocker], [f_warn], [f_nit])
    assert "# Code Review Remediation Feedback" in md_output
    assert "Blocker Findings" in md_output
    assert "core/auth.py:42" in md_output
    assert "Secret leaked" in md_output
    assert "Warning Findings" in md_output
    assert "core/api.py:100" in md_output
    assert "```json" in md_output

    json_data = format_feedback_json(reviews, [f_blocker], [f_warn], [f_nit])
    assert json_data["status"] == "CHANGES_REQUESTED"
    assert json_data["total_findings"] == 3
    assert len(json_data["findings"]["blockers"]) == 1
    assert json_data["findings"]["blockers"][0]["file"] == "core/auth.py"
    assert json_data["findings"]["blockers"][0]["line"] == 42


# ===========================================================================
# 3. PHASE 7 CONSENSUS EXECUTION (FALL A & FALL B)
# ===========================================================================

@pytest.mark.asyncio
async def test_run_consensus_phase_fall_a_re_review_loop():
    """Fall A: When findings exist and retries < max, increment retry and signal revision."""
    state = WorkflowState(task_id="TASK-01", raw_requirements="Reqs")
    assert state.review_retry_count == 0

    f_blocker = Finding(file="main.py", line=15, severity=Severity.BLOCKER, issue="Broken logic")
    reviews = [
        make_sample_review("functional", "CHANGES_REQUESTED", [f_blocker]),
        make_sample_review("architecture", "APPROVED"),
        make_sample_review("security", "APPROVED"),
        make_sample_review("qa_testing", "APPROVED"),
    ]

    decision = await run_consensus_phase(
        summary=reviews,
        state=state,
        max_retries=3,
    )

    assert decision.status == ConsensusStatus.NEEDS_REVISION
    assert decision.approved is False
    assert decision.merged is False
    assert state.review_retry_count == 1
    assert len(state.findings) == 1
    assert state.findings[0].issue == "Broken logic"
    assert "Code Review Remediation Feedback" in decision.feedback_markdown


@pytest.mark.asyncio
async def test_run_consensus_phase_fall_a_circuit_breaker():
    """Fall A: When retry limit reached (retry_count >= max_retries), circuit breaker trips."""
    state = WorkflowState(task_id="TASK-02", raw_requirements="Reqs", review_retry_count=3)

    f_blocker = Finding(file="main.py", line=15, severity=Severity.BLOCKER, issue="Unresolved blocker")
    reviews = [
        make_sample_review("functional", "CHANGES_REQUESTED", [f_blocker]),
        make_sample_review("architecture", "APPROVED"),
        make_sample_review("security", "APPROVED"),
        make_sample_review("qa_testing", "APPROVED"),
    ]

    decision = await run_consensus_phase(
        summary=reviews,
        state=state,
        max_retries=3,
    )

    assert decision.status == ConsensusStatus.REJECTED
    assert decision.approved is False
    assert state.current_phase == WorkflowPhase.FAILED.value
    assert state.metadata.get("review_circuit_breaker") is True


@pytest.mark.asyncio
async def test_run_consensus_phase_fall_b_auto_merge_true():
    """Fall B: When all approved and auto_merge: true, executes remote VCS auto-squash merge."""
    state = WorkflowState(task_id="TASK-03", raw_requirements="Reqs")
    state.metadata["pr_number"] = 123

    reviews = [
        make_sample_review("functional", "APPROVED"),
        make_sample_review("architecture", "APPROVED"),
        make_sample_review("security", "APPROVED"),
        make_sample_review("qa_testing", "APPROVED"),
    ]

    merged_commands = []

    async def mock_runner(binary, *args):
        merged_commands.append((binary, list(args)))
        return 0, "PR #123 merged\n", ""

    vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)

    decision = await run_consensus_phase(
        summary=reviews,
        state=state,
        remote_vcs=vcs,
        auto_merge=True,
    )

    assert decision.status == ConsensusStatus.APPROVED
    assert decision.approved is True
    assert decision.merged is True
    assert decision.merge_mode == "auto"
    assert state.current_phase == WorkflowPhase.DONE.value
    assert state.metadata["merged"] is True
    assert merged_commands == [("gh", ["pr", "merge", "123", "--auto", "--squash"])]


@pytest.mark.asyncio
async def test_run_consensus_phase_fall_b_manual_merge_confirmed():
    """Fall B: When auto_merge: false and user confirms with 'y', executes manual merge."""
    state = WorkflowState(task_id="TASK-04", raw_requirements="Reqs")
    state.metadata["pr_number"] = 456

    reviews = [
        make_sample_review("functional", "APPROVED"),
        make_sample_review("architecture", "APPROVED"),
        make_sample_review("security", "APPROVED"),
        make_sample_review("qa_testing", "APPROVED"),
    ]

    merged_commands = []

    async def mock_runner(binary, *args):
        merged_commands.append((binary, list(args)))
        return 0, "PR #456 merged\n", ""

    vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)

    decision = await run_consensus_phase(
        summary=reviews,
        state=state,
        remote_vcs=vcs,
        auto_merge=False,
        input_func=lambda prompt: "y",
    )

    assert decision.status == ConsensusStatus.APPROVED
    assert decision.approved is True
    assert decision.merged is True
    assert decision.merge_mode == "manual"
    assert state.current_phase == WorkflowPhase.DONE.value
    assert state.metadata["merged"] is True
    assert merged_commands == [("gh", ["pr", "merge", "456", "--squash"])]


@pytest.mark.asyncio
async def test_run_consensus_phase_fall_b_manual_merge_rejected_by_user():
    """Fall B: When auto_merge: false and user inputs 'n', merge is skipped and state is DONE."""
    state = WorkflowState(task_id="TASK-05", raw_requirements="Reqs")

    reviews = [
        make_sample_review("functional", "APPROVED"),
        make_sample_review("architecture", "APPROVED"),
        make_sample_review("security", "APPROVED"),
        make_sample_review("qa_testing", "APPROVED"),
    ]

    merged_commands = []

    async def mock_runner(binary, *args):
        merged_commands.append((binary, list(args)))
        return 0, "", ""

    vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)

    decision = await run_consensus_phase(
        summary=reviews,
        state=state,
        remote_vcs=vcs,
        auto_merge=False,
        input_func=lambda prompt: "n",
    )

    assert decision.status == ConsensusStatus.APPROVED
    assert decision.approved is True
    assert decision.merged is False
    assert decision.merge_mode == "manual"
    assert state.current_phase == WorkflowPhase.DONE.value
    assert state.metadata["merged"] is False
    assert state.metadata.get("merge_skipped_by_user") is True
    assert len(merged_commands) == 0
