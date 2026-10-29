"""Parallel Multi-Persona Code Reviewer for MergeFlow orchestrator.

Coordinates Phase 6 of the orchestration pipeline:
1. Receives the overall git diff and technical specification (SPEC.md).
2. Concurrently launches four isolated reviewer agents via asyncio.gather:
   - Functional & Business Logic (prompts/review_functional.md)
   - Architecture & Code Quality (prompts/review_architecture.md)
   - Application Security (prompts/review_security.md)
   - QA & Test Automation (prompts/review_qa_tests.md)
3. Enforces structured JSON output adhering to ReviewResult schema with json_schema=ReviewResult.
4. Catches parsing failures and conducts at most one retry.
5. Consolidates findings and computes overall approval (requires all 4 reviews APPROVED and 0 BLOCKERs).
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Sequence

from pydantic import ValidationError

from core.models import Finding, ReviewResult, ReviewSummary, Severity
from core.provider import BaseLLMProvider, JSONParseError

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_DIR = Path("prompts")

REVIEWER_PERSONAS: tuple[str, ...] = (
    "functional",
    "architecture",
    "security",
    "qa_testing",
)

PROMPT_FILE_MAP: dict[str, str] = {
    "functional": "review_functional.md",
    "architecture": "review_architecture.md",
    "security": "review_security.md",
    "qa_testing": "review_qa_tests.md",
}


class ReviewError(Exception):
    """Base exception for code review phase errors."""


class ReviewParsingError(ReviewError):
    """Raised when an LLM reviewer output fails JSON schema validation after retries."""


def load_reviewer_prompt(
    reviewer: str,
    prompt_dir: Path | str | None = None,
) -> str:
    """Load the system prompt markdown file for a given reviewer persona.

    Args:
        reviewer: Persona identifier (e.g. 'functional', 'architecture', 'security', 'qa_testing').
        prompt_dir: Directory containing reviewer prompt markdown files.

    Returns:
        The content of the persona review prompt.

    Raises:
        FileNotFoundError: If the specified prompt file cannot be located.
        ValueError: If the reviewer identifier is unknown.
    """
    filename = PROMPT_FILE_MAP.get(reviewer.lower().strip())
    if not filename:
        raise ValueError(
            f"Unknown reviewer persona: '{reviewer}'. "
            f"Available personas: {list(PROMPT_FILE_MAP.keys())}"
        )

    if prompt_dir is not None:
        prompt_path = Path(prompt_dir).resolve() / filename
        if not prompt_path.is_file():
            raise FileNotFoundError(
                f"Reviewer prompt file for persona '{reviewer}' not found at: {prompt_path}"
            )
        return prompt_path.read_text(encoding="utf-8")

    prompt_path = Path(DEFAULT_PROMPT_DIR).resolve() / filename
    if not prompt_path.is_file():
        # Fallback to local prompts directory relative to current working directory
        fallback_path = Path("prompts") / filename
        if fallback_path.is_file():
            prompt_path = fallback_path.resolve()
        else:
            raise FileNotFoundError(
                f"Reviewer prompt file for persona '{reviewer}' not found at: {prompt_path}"
            )

    return prompt_path.read_text(encoding="utf-8")


def _build_review_user_prompt(diff: str, spec: str) -> str:
    """Construct user prompt combining specification and git diff.

    Args:
        diff: Unified git diff string under review.
        spec: Technical specification markdown string.

    Returns:
        Formatted user prompt.
    """
    clean_diff = diff.strip() if diff and diff.strip() else "(Empty diff - no changes detected)"
    clean_spec = spec.strip() if spec and spec.strip() else "(No specification provided)"

    return (
        "# Technical Specification (SPEC.md)\n"
        f"{clean_spec}\n\n"
        "# Git Diff Under Review\n"
        f"{clean_diff}\n\n"
        "Please review the git diff against the technical specification according to your "
        "assigned persona guidelines. Output ONLY a valid JSON object matching ReviewResult."
    )


async def _run_single_review(
    reviewer: str,
    diff: str,
    spec: str,
    provider: BaseLLMProvider,
    prompt_dir: Path | str | None = None,
    max_retries: int = 1,
) -> ReviewResult:
    """Execute a single reviewer agent with retry logic on JSON parsing errors.

    Args:
        reviewer: Persona identifier.
        diff: Unified git diff string.
        spec: Specification markdown string.
        provider: LLM provider instance.
        prompt_dir: Directory where persona prompts reside.
        max_retries: Maximum number of retries upon JSON schema parsing failure (default: 1).

    Returns:
        Validated ReviewResult object.

    Raises:
        ReviewParsingError: If output cannot be parsed after max_retries.
        ReviewError: On execution failure.
    """
    system_prompt = load_reviewer_prompt(reviewer=reviewer, prompt_dir=prompt_dir)
    base_user_prompt = _build_review_user_prompt(diff=diff, spec=spec)

    current_user_prompt = base_user_prompt
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            raw_response = await provider.complete(
                prompt=current_user_prompt,
                system_prompt=system_prompt,
                json_schema=ReviewResult,
            )

            # Validate and convert raw_response into ReviewResult
            if isinstance(raw_response, ReviewResult):
                result = raw_response
            elif isinstance(raw_response, dict):
                result = ReviewResult.model_validate(raw_response)
            elif isinstance(raw_response, str):
                result = BaseLLMProvider._extract_and_parse_json(raw_response, ReviewResult)
            else:
                raise ReviewParsingError(f"Unexpected provider response type: {type(raw_response)}")

            # Enforce canonical reviewer persona identifier
            result.reviewer = reviewer

            logger.info(
                "Reviewer '%s' completed successfully on attempt %d: status=%s, findings=%d",
                reviewer,
                attempt + 1,
                result.status,
                len(result.findings),
            )
            return result

        except (JSONParseError, ValidationError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries:
                logger.warning(
                    "Reviewer '%s' JSON schema validation failed on attempt %d (%s). "
                    "Retrying (attempt %d of %d)...",
                    reviewer,
                    attempt + 1,
                    exc,
                    attempt + 2,
                    max_retries + 1,
                )
                current_user_prompt = (
                    f"{base_user_prompt}\n\n"
                    f"[ERROR FROM PREVIOUS ATTEMPT]:\n"
                    f"Your previous response was rejected because it failed JSON schema validation:\n"
                    f"{exc}\n\n"
                    f"CRITICAL: You must output ONLY a valid JSON object matching the ReviewResult schema. "
                    f"Do not wrap in markdown quotes or add conversational text."
                )
            else:
                logger.error(
                    "Reviewer '%s' failed after %d retries: %s",
                    reviewer,
                    max_retries,
                    exc,
                )
                raise ReviewParsingError(
                    f"Reviewer '{reviewer}' failed to return valid ReviewResult after "
                    f"{max_retries} retry: {exc}"
                ) from exc

    raise ReviewParsingError(
        f"Reviewer '{reviewer}' failed to produce valid ReviewResult: {last_error}"
    )


async def run_multi_review(
    diff: str,
    spec: str,
    provider: BaseLLMProvider,
    reviewers: Sequence[str] | None = None,
    prompt_dir: Path | str | None = None,
) -> list[ReviewResult]:
    """Execute all reviewer personas in parallel using asyncio.gather.

    Args:
        diff: Unified git diff string representing proposed changes.
        spec: Technical specification markdown string.
        provider: LLM provider instance for generating completions.
        reviewers: Optional custom sequence of reviewer personas. Defaults to all 4 standard personas.
        prompt_dir: Optional custom prompt directory path.

    Returns:
        List of ReviewResult objects, one for each reviewer persona.

    Raises:
        ReviewParsingError: If any reviewer fails to return valid JSON after retry.
        ReviewError: If an unexpected error occurs during review execution.
    """
    target_reviewers = list(reviewers) if reviewers is not None else list(REVIEWER_PERSONAS)

    logger.info(
        "Initiating parallel multi-persona review with personas: %s",
        target_reviewers,
    )

    tasks = [
        _run_single_review(
            reviewer=persona,
            diff=diff,
            spec=spec,
            provider=provider,
            prompt_dir=prompt_dir,
            max_retries=1,
        )
        for persona in target_reviewers
    ]

    results = await asyncio.gather(*tasks)
    return list(results)


def is_overall_approved(reviews: Sequence[ReviewResult]) -> bool:
    """Determine whether the aggregated multi-persona reviews meet approval criteria.

    An overall approval is granted ONLY if:
    1. Every review has status == "APPROVED".
    2. Zero findings across all reviews have severity == Severity.BLOCKER.

    Args:
        reviews: Sequence of ReviewResult objects from all personas.

    Returns:
        True if all reviews are approved and no blocker findings exist; False otherwise.
    """
    if not reviews:
        return False

    all_status_approved = all(r.status == "APPROVED" for r in reviews)
    has_blockers = any(
        f.severity == Severity.BLOCKER for r in reviews for f in r.findings
    )
    return all_status_approved and not has_blockers


def format_review_report(
    reviews: Sequence[ReviewResult],
    is_approved: bool,
    blockers: Sequence[Finding],
    warnings: Sequence[Finding],
    nits: Sequence[Finding],
) -> str:
    """Generate a formatted markdown report of all review outcomes and findings.

    Args:
        reviews: Sequence of individual ReviewResult objects.
        is_approved: Overall approval status.
        blockers: Consolidated list of BLOCKER findings.
        warnings: Consolidated list of WARNING findings.
        nits: Consolidated list of NIT findings.

    Returns:
        Formatted markdown summary report.
    """
    status_icon = "APPROVED" if is_approved else "CHANGES_REQUESTED"

    lines = [
        "# Multi-Persona Code Review Summary",
        "",
        f"**Overall Status:** `{status_icon}`",
        f"**Blockers:** {len(blockers)} | **Warnings:** {len(warnings)} | **Nits:** {len(nits)}",
        "",
        "## Reviewer Breakdown",
        "",
        "| Reviewer | Status | Findings | Summary |",
        "|---|---|---|---|",
    ]

    for r in reviews:
        lines.append(f"| **{r.reviewer}** | `{r.status}` | {len(r.findings)} | {r.summary} |")

    lines.append("")

    if blockers:
        lines.append("## Blocker Findings (Must Be Fixed)")
        for f in blockers:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"- **[{loc}]** {f.issue}")
            if f.suggestion:
                lines.append(f"  *Suggestion:* {f.suggestion}")
        lines.append("")

    if warnings:
        lines.append("## Warning Findings")
        for f in warnings:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"- **[{loc}]** {f.issue}")
            if f.suggestion:
                lines.append(f"  *Suggestion:* {f.suggestion}")
        lines.append("")

    if nits:
        lines.append("## Minor / Nit Findings")
        for f in nits:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"- **[{loc}]** {f.issue}")
            if f.suggestion:
                lines.append(f"  *Suggestion:* {f.suggestion}")
        lines.append("")

    return "\n".join(lines)


def consolidate_reviews(reviews: Sequence[ReviewResult]) -> ReviewSummary:
    """Consolidate findings and approval status across all persona reviews.

    Args:
        reviews: Sequence of ReviewResult objects.

    Returns:
        ReviewSummary aggregating findings, blocker checks, and overall approval status.
    """
    review_list = list(reviews)
    all_approved = len(review_list) > 0 and all(r.status == "APPROVED" for r in review_list)
    all_findings = [f for r in review_list for f in r.findings]

    blockers = [f for f in all_findings if f.severity == Severity.BLOCKER]
    warnings = [f for f in all_findings if f.severity == Severity.WARNING]
    nits = [f for f in all_findings if f.severity == Severity.NIT]

    has_blockers = len(blockers) > 0
    overall_approved = all_approved and not has_blockers

    markdown_report = format_review_report(
        reviews=review_list,
        is_approved=overall_approved,
        blockers=blockers,
        warnings=warnings,
        nits=nits,
    )

    return ReviewSummary(
        all_approved=all_approved,
        has_blockers=has_blockers,
        is_approved=overall_approved,
        total_findings=len(all_findings),
        blockers=blockers,
        warnings=warnings,
        nits=nits,
        reviews=review_list,
        markdown_report=markdown_report,
    )


async def run_review_phase(
    diff: str,
    spec: str,
    provider: BaseLLMProvider,
    reviewers: Sequence[str] | None = None,
    prompt_dir: Path | str | None = None,
) -> ReviewSummary:
    """Orchestrate Phase 6 multi-persona review and return consolidated summary.

    Args:
        diff: Unified git diff string.
        spec: Technical specification markdown string.
        provider: LLM provider instance.
        reviewers: Optional custom sequence of reviewer personas.
        prompt_dir: Optional custom prompt directory path.

    Returns:
        ReviewSummary aggregating all findings and overall approval status.
    """
    reviews = await run_multi_review(
        diff=diff,
        spec=spec,
        provider=provider,
        reviewers=reviewers,
        prompt_dir=prompt_dir,
    )
    return consolidate_reviews(reviews)
