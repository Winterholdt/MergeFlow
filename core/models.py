"""Domain and state data models for MergeFlow orchestrator.

Defines Pydantic models for workflow state tracking, specifications, code review
results, and diagnostic findings.
"""

from enum import Enum
import uuid
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


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


class Severity(str, Enum):
    """Severity levels for code review findings."""

    BLOCKER = "BLOCKER"
    WARNING = "WARNING"
    NIT = "NIT"


FindingSeverity = Severity


class Finding(BaseModel):
    """Represents a code review finding or diagnostic issue."""

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the finding.",
    )
    file: str = Field(
        ...,
        description="Path of the affected file relative to repository root.",
    )
    line: int | None = Field(
        default=None,
        ge=1,
        description="Line number in the affected file, if applicable.",
    )
    severity: Severity = Field(
        ...,
        description="Severity level of the finding (BLOCKER, WARNING, NIT).",
    )
    issue: str = Field(
        ...,
        description="Clear description of the issue or concern.",
    )
    suggestion: str = Field(
        default="",
        description="Actionable suggestion or code patch to resolve the issue.",
    )

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, v: Any) -> Severity | str:
        if isinstance(v, str):
            v_clean = v.strip().upper()
            if v_clean in {"BLOCKER", "CRITICAL", "HIGH"}:
                return Severity.BLOCKER
            if v_clean in {"WARNING", "WARN", "MEDIUM"}:
                return Severity.WARNING
            if v_clean in {"NIT", "INFO", "LOW"}:
                return Severity.NIT
        return v

    @model_validator(mode="before")
    @classmethod
    def _map_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "file" not in data and "file_path" in data:
                data["file"] = data.get("file_path") or ""
            if "line" not in data and "line_number" in data:
                data["line"] = data.get("line_number")
            if "issue" not in data:
                data["issue"] = data.get("title") or data.get("description") or ""
            if "suggestion" not in data:
                data["suggestion"] = data.get("suggestion") or ""
            if "file" not in data:
                data["file"] = ""
            if "severity" not in data:
                data["severity"] = Severity.WARNING
        return data

    @property
    def file_path(self) -> str:
        return self.file

    @property
    def line_number(self) -> int | None:
        return self.line

    @property
    def title(self) -> str:
        return self.issue

    @property
    def description(self) -> str:
        return self.issue


class ReviewResult(BaseModel):
    """Evaluation result emitted by an individual persona review agent."""

    reviewer: str = Field(
        ...,
        description="Identifier of the review persona (e.g., functional, architecture, security, qa_testing).",
    )
    status: Literal["APPROVED", "CHANGES_REQUESTED"] = Field(
        ...,
        description="Approval decision: APPROVED or CHANGES_REQUESTED.",
    )
    summary: str = Field(
        ...,
        description="Evaluation summary explaining the review findings and outcome.",
    )
    findings: list[Finding] = Field(
        default_factory=list,
        description="List of detected findings or issues.",
    )

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, v: Any) -> str:
        if isinstance(v, str):
            v_clean = v.strip().upper()
            if v_clean in {"APPROVED", "APPROVE", "PASS", "PASSED"}:
                return "APPROVED"
            if v_clean in {
                "CHANGES_REQUESTED",
                "CHANGE_REQUESTED",
                "REJECTED",
                "REJECT",
                "FAIL",
                "FAILED",
            }:
                return "CHANGES_REQUESTED"
        return v

    @model_validator(mode="before")
    @classmethod
    def _map_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "status" not in data and "approved" in data:
                data["status"] = "APPROVED" if data["approved"] else "CHANGES_REQUESTED"
            if "reviewer" not in data:
                data["reviewer"] = "reviewer"
        return data

    @property
    def approved(self) -> bool:
        """Convenience property indicating whether review was approved."""
        return self.status == "APPROVED"


class ReviewSummary(BaseModel):
    """Aggregated multi-persona review summary."""

    all_approved: bool = Field(
        ...,
        description="Whether all individual persona reviews have status APPROVED.",
    )
    has_blockers: bool = Field(
        ...,
        description="Whether any finding has severity BLOCKER.",
    )
    is_approved: bool = Field(
        ...,
        description="Overall approval: True if all reviews APPROVED and zero BLOCKER findings.",
    )
    total_findings: int = Field(
        default=0,
        description="Total number of findings across all reviews.",
    )
    blockers: list[Finding] = Field(
        default_factory=list,
        description="All findings with severity BLOCKER.",
    )
    warnings: list[Finding] = Field(
        default_factory=list,
        description="All findings with severity WARNING.",
    )
    nits: list[Finding] = Field(
        default_factory=list,
        description="All findings with severity NIT.",
    )
    reviews: list[ReviewResult] = Field(
        default_factory=list,
        description="Individual persona review results.",
    )
    markdown_report: str = Field(
        default="",
        description="Human-readable markdown summary report.",
    )

    @property
    def approved(self) -> bool:
        """Alias for is_approved."""
        return self.is_approved


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
