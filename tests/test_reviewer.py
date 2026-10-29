"""Unit tests for Phase 6 Multi-Persona Code Reviewer."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.models import Finding, ReviewResult, ReviewSummary, Severity
from core.provider import BaseLLMProvider, JSONParseError
from core.reviewer import (
    REVIEWER_PERSONAS,
    ReviewError,
    ReviewParsingError,
    consolidate_reviews,
    format_review_report,
    is_overall_approved,
    load_reviewer_prompt,
    run_multi_review,
    run_review_phase,
)


class MockLLMProvider(BaseLLMProvider):
    """Configurable mock provider for testing reviewer execution."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses or [])
        self.call_history: list[dict[str, Any]] = []

    async def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        json_schema: Any = None,
    ) -> Any:
        self.call_history.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "json_schema": json_schema,
            }
        )
        if not self.responses:
            return ReviewResult(
                reviewer="mock",
                status="APPROVED",
                summary="Default mock approval",
                findings=[],
            )
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


# ============================================================================
# 1. PROMPT LOADING TESTS
# ============================================================================

def test_load_all_reviewer_prompts():
    """Verify that all 4 reviewer prompts exist and contain valid instructions."""
    for persona in REVIEWER_PERSONAS:
        content = load_reviewer_prompt(persona)
        assert len(content) > 100
        assert "ReviewResult" in content
        assert "BLOCKER" in content
        assert "APPROVED" in content


def test_load_reviewer_prompt_invalid_persona():
    """Verify ValueError is raised for an unrecognized reviewer persona."""
    with pytest.raises(ValueError, match="Unknown reviewer persona"):
        load_reviewer_prompt("unknown_persona")


def test_load_reviewer_prompt_missing_file(tmp_path: Path):
    """Verify FileNotFoundError is raised when prompt directory does not contain file."""
    with pytest.raises(FileNotFoundError):
        load_reviewer_prompt("functional", prompt_dir=tmp_path)


# ============================================================================
# 2. APPROVAL & CONSOLIDATION LOGIC TESTS
# ============================================================================

def test_is_overall_approved_all_approved_no_blockers():
    """Review passes when all 4 reviewers approve and zero blockers exist."""
    reviews = [
        ReviewResult(
            reviewer=persona,
            status="APPROVED",
            summary="Looks good",
            findings=[
                Finding(file="a.py", severity=Severity.WARNING, issue="Minor style")
            ],
        )
        for persona in REVIEWER_PERSONAS
    ]
    assert is_overall_approved(reviews) is True


def test_is_overall_approved_with_blocker_finding():
    """Review fails if ANY reviewer reports a BLOCKER finding, even if status is APPROVED."""
    reviews = [
        ReviewResult(
            reviewer="functional",
            status="APPROVED",
            summary="Ok",
            findings=[
                Finding(
                    file="main.py",
                    severity=Severity.BLOCKER,
                    issue="Critical logic error",
                )
            ],
        ),
        ReviewResult(reviewer="architecture", status="APPROVED", summary="Ok"),
        ReviewResult(reviewer="security", status="APPROVED", summary="Ok"),
        ReviewResult(reviewer="qa_testing", status="APPROVED", summary="Ok"),
    ]
    assert is_overall_approved(reviews) is False


def test_is_overall_approved_changes_requested():
    """Review fails if any reviewer requests changes."""
    reviews = [
        ReviewResult(reviewer="functional", status="APPROVED", summary="Ok"),
        ReviewResult(
            reviewer="architecture",
            status="CHANGES_REQUESTED",
            summary="Refactor needed",
            findings=[
                Finding(
                    file="arch.py",
                    severity=Severity.WARNING,
                    issue="Coupling too high",
                )
            ],
        ),
        ReviewResult(reviewer="security", status="APPROVED", summary="Ok"),
        ReviewResult(reviewer="qa_testing", status="APPROVED", summary="Ok"),
    ]
    assert is_overall_approved(reviews) is False


def test_is_overall_approved_empty_list():
    """Empty reviews list must not be approved."""
    assert is_overall_approved([]) is False


def test_consolidate_reviews():
    """Verify consolidation of blockers, warnings, nits, and markdown reporting."""
    f_blocker = Finding(
        file="core/auth.py",
        line=10,
        severity=Severity.BLOCKER,
        issue="SQL injection",
        suggestion="Use prepared statement",
    )
    f_warn = Finding(
        file="core/db.py",
        line=25,
        severity=Severity.WARNING,
        issue="Missing connection pool limit",
        suggestion="Set pool_size=10",
    )
    f_nit = Finding(
        file="core/util.py",
        severity=Severity.NIT,
        issue="Missing type hint on helper",
        suggestion="Add str return annotation",
    )

    reviews = [
        ReviewResult(
            reviewer="security",
            status="CHANGES_REQUESTED",
            summary="Vulnerabilities present",
            findings=[f_blocker],
        ),
        ReviewResult(
            reviewer="architecture",
            status="APPROVED",
            summary="Architecture acceptable",
            findings=[f_warn],
        ),
        ReviewResult(
            reviewer="functional",
            status="APPROVED",
            summary="Functional requirements met",
            findings=[f_nit],
        ),
        ReviewResult(
            reviewer="qa_testing",
            status="APPROVED",
            summary="Tests complete",
            findings=[],
        ),
    ]

    summary = consolidate_reviews(reviews)
    assert summary.all_approved is False
    assert summary.has_blockers is True
    assert summary.is_approved is False
    assert summary.total_findings == 3
    assert summary.blockers == [f_blocker]
    assert summary.warnings == [f_warn]
    assert summary.nits == [f_nit]
    assert "Multi-Persona Code Review Summary" in summary.markdown_report
    assert "core/auth.py:10" in summary.markdown_report
    assert "SQL injection" in summary.markdown_report


# ============================================================================
# 3. PARALLEL MULTI-REVIEW EXECUTION TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_run_multi_review_success():
    """Verify run_multi_review executes all 4 personas in parallel with json_schema."""
    provider = MockLLMProvider()

    diff = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-def old(): pass\n+def new(): pass"
    spec = "# Spec\nImplement new feature."

    results = await run_multi_review(diff=diff, spec=spec, provider=provider)

    assert len(results) == 4
    reviewer_names = [r.reviewer for r in results]
    for persona in REVIEWER_PERSONAS:
        assert persona in reviewer_names

    # Verify provider was called 4 times, each with json_schema=ReviewResult
    assert len(provider.call_history) == 4
    for call in provider.call_history:
        assert call["json_schema"] == ReviewResult
        assert "Git Diff Under Review" in call["prompt"]
        assert "Technical Specification" in call["prompt"]


@pytest.mark.asyncio
async def test_run_multi_review_retry_on_parse_error():
    """Verify that a JSON parsing error triggers exactly one retry before succeeding."""
    success_result = ReviewResult(
        reviewer="functional",
        status="APPROVED",
        summary="Retried and passed",
        findings=[],
    )

    # First call fails with JSONParseError, second succeeds
    provider = MockLLMProvider(
        responses=[
            JSONParseError("Failed to parse JSON schema"),
            success_result,
        ]
    )

    # Run single reviewer to test retry logic in isolation
    from core.reviewer import _run_single_review

    res = await _run_single_review(
        reviewer="functional",
        diff="diff --git",
        spec="spec",
        provider=provider,
        max_retries=1,
    )

    assert res.status == "APPROVED"
    assert res.summary == "Retried and passed"
    assert len(provider.call_history) == 2
    assert "ERROR FROM PREVIOUS ATTEMPT" in provider.call_history[1]["prompt"]


@pytest.mark.asyncio
async def test_run_multi_review_max_retries_exceeded():
    """Verify that max retries (1 retry = 2 attempts total) raises ReviewParsingError."""
    provider = MockLLMProvider(
        responses=[
            JSONParseError("Bad JSON attempt 1"),
            JSONParseError("Bad JSON attempt 2"),
        ]
    )

    from core.reviewer import _run_single_review

    with pytest.raises(ReviewParsingError, match="failed to return valid ReviewResult"):
        await _run_single_review(
            reviewer="security",
            diff="diff --git",
            spec="spec",
            provider=provider,
            max_retries=1,
        )

    # Exactly 2 attempts (initial + 1 retry)
    assert len(provider.call_history) == 2


@pytest.mark.asyncio
async def test_run_multi_review_dict_and_string_parsing():
    """Verify that provider responses returning dict or JSON string are handled properly."""
    dict_response = {
        "reviewer": "architecture",
        "status": "APPROVED",
        "summary": "Clean architecture",
        "findings": [],
    }
    string_response = (
        '{"reviewer": "qa_testing", "status": "APPROVED", "summary": "Great tests", "findings": []}'
    )

    provider = MockLLMProvider(responses=[dict_response, string_response])

    from core.reviewer import _run_single_review

    r1 = await _run_single_review(
        reviewer="architecture",
        diff="",
        spec="",
        provider=provider,
    )
    assert r1.reviewer == "architecture"
    assert r1.status == "APPROVED"

    r2 = await _run_single_review(
        reviewer="qa_testing",
        diff="",
        spec="",
        provider=provider,
    )
    assert r2.reviewer == "qa_testing"
    assert r2.status == "APPROVED"


# ============================================================================
# 4. ORCHESTRATION PHASE END-TO-END TEST
# ============================================================================

@pytest.mark.asyncio
async def test_run_review_phase_orchestration():
    """Verify run_review_phase runs multi-review and returns ReviewSummary."""
    provider = MockLLMProvider()

    summary = await run_review_phase(
        diff="diff --git a/test.py b/test.py",
        spec="# Spec\nRequirements",
        provider=provider,
    )

    assert isinstance(summary, ReviewSummary)
    assert summary.all_approved is True
    assert summary.is_approved is True
    assert len(summary.reviews) == 4
