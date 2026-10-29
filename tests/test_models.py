"""Unit tests for domain and state models."""

import pytest
from pydantic import ValidationError

from core.models import (
    Finding,
    FindingSeverity,
    ReviewResult,
    Specification,
    WorkflowPhase,
    WorkflowState,
)


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
    assert finding.severity == FindingSeverity.MEDIUM
    assert finding.line_number == 42

    with pytest.raises(ValidationError):
        Finding(title="Error", description="Invalid", line_number=0)


def test_review_result_serialization():
    """Verify ReviewResult serialization and parsing."""
    result = ReviewResult(
        approved=False,
        summary="Found 1 blocker issue.",
        findings=[
            Finding(
                severity=FindingSeverity.HIGH,
                title="Security vulnerability",
                description="Hardcoded secret detected",
            )
        ],
        score=65.5,
    )
    data = result.model_dump()
    assert data["approved"] is False
    assert len(data["findings"]) == 1
    assert data["findings"][0]["severity"] == "HIGH"

    restored = ReviewResult.model_validate(data)
    assert restored.approved == result.approved
    assert restored.findings[0].title == "Security vulnerability"


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
