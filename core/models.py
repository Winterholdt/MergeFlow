"""Domain and state data models for MergeFlow orchestrator.

Defines Pydantic models for workflow state tracking, specifications, code review
results, and diagnostic findings.
"""

from enum import Enum
import uuid
from typing import Any
from pydantic import BaseModel, Field


class WorkflowPhase(str, Enum):
    """Execution phases in the autonomous agent workflow."""

    SPEC_DISCOVERY = "SPEC_DISCOVERY"
    CODING = "CODING"
    CI_LOOP = "CI_LOOP"
    REVIEW = "REVIEW"
    DONE = "DONE"
    FAILED = "FAILED"


class PipelineState(str, Enum):
    """States of remote CI pipeline or PR checks."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class PipelineCheck(BaseModel):
    """Individual CI check or pipeline step evaluation."""

    name: str = Field(
        ...,
        description="Name of the pipeline check or workflow step.",
    )
    status: str = Field(
        default="completed",
        description="Execution status of the check (e.g., completed, in_progress, queued).",
    )
    conclusion: str | None = Field(
        default=None,
        description="Conclusion outcome of the check (e.g., success, failure, neutral, cancelled).",
    )
    details_url: str | None = Field(
        default=None,
        description="Direct URL to logs or details for this check.",
    )


class PipelineStatus(BaseModel):
    """Aggregated status of remote CI checks or GitLab pipelines."""

    state: PipelineState | str = Field(
        default=PipelineState.PENDING,
        description="Aggregated status of all checks.",
    )
    all_passed: bool = Field(
        default=False,
        description="Whether all required checks passed successfully.",
    )
    checks: list[PipelineCheck] = Field(
        default_factory=list,
        description="Individual status of each check item.",
    )
    url: str | None = Field(
        default=None,
        description="URL to the remote PR, MR, or pipeline dashboard.",
    )
    raw_output: str = Field(
        default="",
        description="Raw output produced by the CLI tool during inspection.",
    )


class FindingSeverity(str, Enum):
    """Severity levels for review or static analysis findings."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Finding(BaseModel):
    """Represents a diagnostic issue, bug, or improvement recommendation."""

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the finding.",
    )
    severity: FindingSeverity | str = Field(
        default=FindingSeverity.MEDIUM,
        description="Severity level of the finding.",
    )
    title: str = Field(
        ...,
        description="Brief headline describing the finding.",
    )
    description: str = Field(
        ...,
        description="Detailed description of the issue or improvement opportunity.",
    )
    file_path: str | None = Field(
        default=None,
        description="Relative or absolute path to the affected file.",
    )
    line_number: int | None = Field(
        default=None,
        ge=1,
        description="Line number in the affected file, if applicable.",
    )
    suggestion: str | None = Field(
        default=None,
        description="Concrete suggestion or code patch to resolve the issue.",
    )


class ReviewResult(BaseModel):
    """Evaluation result emitted by the review agent."""

    approved: bool = Field(
        ...,
        description="Whether the reviewed code meets quality standards and passes review.",
    )
    summary: str = Field(
        ...,
        description="High-level evaluation summary.",
    )
    findings: list[Finding] = Field(
        default_factory=list,
        description="List of detected findings or issues.",
    )
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Optional quality score (0-100).",
    )


class Specification(BaseModel):
    """Structured specification discovered from raw requirements."""

    task_id: str = Field(
        ...,
        description="Identifier linking to the originating task.",
    )
    title: str = Field(
        ...,
        description="Title of the specification.",
    )
    summary: str = Field(
        ...,
        description="Overview and objective of the specification.",
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="Testable conditions required for completion.",
    )
    technical_notes: str | None = Field(
        default=None,
        description="Architectural considerations and implementation guidance.",
    )
    raw_markdown: str | None = Field(
        default=None,
        description="Full markdown representation of the specification.",
    )


class WorkflowState(BaseModel):
    """Mutable state object tracking the lifecycle of an autonomous agent task."""

    task_id: str = Field(
        ...,
        description="Unique identifier for the orchestration task.",
    )
    raw_requirements: str = Field(
        ...,
        description="Original user or system requirements.",
    )
    spec_markdown: str | None = Field(
        default=None,
        description="Generated specification document in Markdown format.",
    )
    branch_name: str | None = Field(
        default=None,
        description="Active git working branch for code modifications.",
    )
    current_phase: str = Field(
        default=WorkflowPhase.SPEC_DISCOVERY.value,
        description="Current workflow phase (e.g., SPEC_DISCOVERY, CODING, CI_LOOP, REVIEW, DONE, FAILED).",
    )
    ci_retry_count: int = Field(
        default=0,
        ge=0,
        description="Number of CI remediation attempts made so far.",
    )
    review_retry_count: int = Field(
        default=0,
        ge=0,
        description="Number of review remediation attempts made so far.",
    )
    findings: list[Finding] = Field(
        default_factory=list,
        description="Accumulated or active findings from CI/Review phases.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary additional context or metadata.",
    )

    def transition_to(self, phase: WorkflowPhase | str) -> None:
        """Update current phase ensuring string representation.

        Args:
            phase: Target workflow phase enum or string.
        """
        self.current_phase = phase.value if isinstance(phase, WorkflowPhase) else str(phase)

    def increment_ci_retry(self) -> int:
        """Increment and return the CI retry counter."""
        self.ci_retry_count += 1
        return self.ci_retry_count

    def increment_review_retry(self) -> int:
        """Increment and return the review retry counter."""
        self.review_retry_count += 1
        return self.review_retry_count

    def add_finding(self, finding: Finding) -> None:
        """Add a finding to the state's finding collection."""
        self.findings.append(finding)

    def clear_findings(self) -> None:
        """Clear all active findings."""
        self.findings.clear()
