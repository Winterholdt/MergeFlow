"""Unit tests for domain and state models."""

import pytest
from pydantic import ValidationError

from core.models import (
    Finding,
    FindingSeverity,
    ReviewResult,
    ReviewSummary,
    Severity,
    Specification,
    WorkflowPhase,
    WorkflowState,
)


def test_severity_enum():
    """Verify Severity enum values."""
    assert Severity.BLOCKER.value == "BLOCKER"
    assert Severity.WARNING.value == "WARNING"
    assert Severity.NIT.value == "NIT"
    assert FindingSeverity == Severity


def test_finding_schema_and_normalization():
    """Verify Finding model adheres to required schema."""
    finding = Finding(
        file="core/main.py",
        line=42,
        severity=Severity.BLOCKER,
        issue="Null pointer exception possible",
        suggestion="Add if check for None",
    )
    assert finding.file == "core/main.py"
    assert finding.line == 42
    assert finding.severity == Severity.BLOCKER
    assert finding.issue == "Null pointer exception possible"
    assert finding.suggestion == "Add if check for None"

    # Case normalization
    f_warn = Finding(
        file="core/util.py",
        severity="warning",
        issue="Missing docstring",
    )
    assert f_warn.severity == Severity.WARNING
    assert f_warn.line is None
    assert f_warn.suggestion == ""


def test_review_result_schema():
    """Verify ReviewResult model with reviewer, status, summary, and findings."""
    res = ReviewResult(
        reviewer="functional",
        status="APPROVED",
        summary="All functional acceptance criteria verified.",
        findings=[],
    )
    assert res.reviewer == "functional"
    assert res.status == "APPROVED"
    assert res.approved is True
    assert res.findings == []

    # Case normalization for status
    res2 = ReviewResult(
        reviewer="security",
        status="changes_requested",
        summary="Security issues detected",
        findings=[
            Finding(
                file="core/auth.py",
                line=12,
                severity=Severity.BLOCKER,
                issue="Hardcoded API secret",
                suggestion="Use environment variable",
            )
        ],
    )
    assert res2.status == "CHANGES_REQUESTED"
    assert res2.approved is False
    assert len(res2.findings) == 1
    assert res2.findings[0].severity == Severity.BLOCKER


def test_review_summary_model():
    """Verify ReviewSummary properties and consolidation schema."""
    f1 = Finding(file="a.py", severity=Severity.BLOCKER, issue="Crash bug")
    f2 = Finding(file="b.py", severity=Severity.WARNING, issue="Unused import")
    r1 = ReviewResult(
        reviewer="functional",
        status="CHANGES_REQUESTED",
        summary="Failed",
        findings=[f1],
    )
    r2 = ReviewResult(
        reviewer="architecture",
        status="APPROVED",
        summary="Passed",
        findings=[f2],
    )
    summary = ReviewSummary(
        all_approved=False,
        has_blockers=True,
        is_approved=False,
        total_findings=2,
        blockers=[f1],
        warnings=[f2],
        nits=[],
        reviews=[r1, r2],
    )
    assert summary.all_approved is False
    assert summary.has_blockers is True
    assert summary.is_approved is False
    assert summary.approved is False
    assert len(summary.blockers) == 1


def test_finding_defaults_and_validation():
    """Verify Finding model field defaults and validation."""
    finding = Finding(
        title="Syntax error",
        description="Missing colon at end of if-statement",
        file_path="src/main.py",
        line_number=42,
        suggestion="Add ':' after condition",
    )
    assert finding.id is not None
    assert finding.severity == Severity.WARNING
    assert finding.line_number == 42

    with pytest.raises(ValidationError):
        Finding(file="test.py", severity=Severity.BLOCKER, issue="Err", line=0)


def test_review_result_serialization():
    """Verify ReviewResult serialization and parsing."""
    result = ReviewResult(
        reviewer="security",
        status="CHANGES_REQUESTED",
        summary="Found 1 blocker issue.",
        findings=[
            Finding(
                severity=Severity.BLOCKER,
                file="core/secret.py",
                issue="Security vulnerability: Hardcoded secret detected",
                suggestion="Use environment variables",
            )
        ],
    )
    data = result.model_dump()
    assert data["status"] == "CHANGES_REQUESTED"
    assert result.approved is False
    assert len(data["findings"]) == 1
    assert data["findings"][0]["severity"] == "BLOCKER"

    restored = ReviewResult.model_validate(data)
    assert restored.approved == result.approved
    assert restored.findings[0].issue == "Security vulnerability: Hardcoded secret detected"


def test_specification_model():
    """Verify Specification data model."""
    spec = Specification(
        task_id="TASK-101",
        title="Implement Phase 1",
        summary="Create base classes and config",
        acceptance_criteria=["Unit tests pass", "CLI and API adapters work"],
        technical_notes="Use Pydantic 2.x and asyncio",
    )
    assert spec.task_id == "TASK-101"
    assert len(spec.acceptance_criteria) == 2


def test_workflow_state_transitions_and_counters():
    """Verify WorkflowState lifecycle, transitions, and retry counters."""
    state = WorkflowState(
        task_id="TASK-202",
        raw_requirements="Implement autonomous orchestrator",
    )
    assert state.task_id == "TASK-202"
    assert state.current_phase == WorkflowPhase.SPEC_DISCOVERY.value
    assert state.ci_retry_count == 0
    assert state.review_retry_count == 0
    assert state.findings == []

    # Phase transition using Enum
    state.transition_to(WorkflowPhase.CODING)
    assert state.current_phase == "CODING"

    # Phase transition using string
    state.transition_to("CI_LOOP")
    assert state.current_phase == "CI_LOOP"

    # Counter increments
    assert state.increment_ci_retry() == 1
    assert state.increment_ci_retry() == 2
    assert state.increment_review_retry() == 1

    # Findings management
    finding = Finding(title="Typo", description="Fix variable name")
    state.add_finding(finding)
    assert len(state.findings) == 1
    assert state.findings[0].title == "Typo"

    state.clear_findings()
    assert len(state.findings) == 0


def test_workflow_state_validation_constraints():
    """Verify validation on negative counters in WorkflowState."""
    with pytest.raises(ValidationError):
        WorkflowState(
            task_id="TASK-999",
            raw_requirements="Test",
            ci_retry_count=-1,
        )
