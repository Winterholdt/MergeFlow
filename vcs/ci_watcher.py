"""Remote CI pipeline watcher and log extraction engine for MergeFlow.

Monitors remote pipeline execution (GitHub Actions via `gh`, GitLab CI via `glab`),
polls until completion, and automatically extracts and cleans failure logs/stacktraces
for the CI self-healing loop.
"""

import asyncio
from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, Field

from core.models import PipelineCheck, PipelineState, PipelineStatus
from vcs.remote_vcs import RemoteVCS, RemoteVCSError, VCSType

logger = logging.getLogger(__name__)

# ANSI escape sequence regex pattern
ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Timestamp prefix pattern (e.g. 2026-10-29T16:50:00.1234567Z )
TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\s*",
    re.MULTILINE,
)

# GitHub actions runner group annotations (e.g. ##[group]...##[endgroup] or ##[debug])
RUNNER_TAG_PATTERN = re.compile(r"^##\[(?:group|endgroup|debug|command)\][^\r\n]*$", re.MULTILINE)

# Noisy dependency / environment download lines
NOISE_LINE_PATTERNS = [
    re.compile(r"^\s*(?:Collecting|Downloading|Using cached|Requirement already satisfied:)\s+", re.IGNORECASE),
    re.compile(r"^\s*(?:Fetched|Resolving dependencies\.\.\.|Receiving objects:|Resolving deltas:)\s+", re.IGNORECASE),
    re.compile(r"^\s*\|\s*[\#\s]+\|\s*\d+%", re.IGNORECASE),  # progress bars
    re.compile(r"^\s*CACHED \[\d+/\d+\]", re.IGNORECASE),
]


class PipelineResult(BaseModel):
    """Evaluation result of remote pipeline monitoring."""

    status: PipelineState | str = Field(
        ...,
        description="Final pipeline state (SUCCESS, FAILED, UNKNOWN).",
    )
    passed: bool = Field(
        ...,
        description="Whether the pipeline succeeded without failures.",
    )
    error_logs: str = Field(
        default="",
        description="Filtered, concise stacktrace and failure logs relevant for the healing agent.",
    )
    raw_logs: str = Field(
        default="",
        description="Complete raw log output received from the remote VCS CLI.",
    )
    checks: list[PipelineCheck] = Field(
        default_factory=list,
        description="List of individual checks or workflow jobs.",
    )
    details_url: str | None = Field(
        default=None,
        description="Web URL to the pipeline dashboard or failed job run.",
    )
    duration_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Total duration spent polling in seconds.",
    )


def filter_ci_noise(log_text: str) -> str:
    """Filter noise, ANSI codes, runner annotations, and progress bars from CI logs.

    Args:
        log_text: Raw CI log text.

    Returns:
        Cleaned log text.
    """
    if not log_text:
        return ""

    # 1. Strip ANSI escape sequences (terminal formatting and colors)
    clean = ANSI_ESCAPE_PATTERN.sub("", log_text)

    # 2. Strip GitHub Actions runner tags (##[group], ##[debug], etc.)
    clean = RUNNER_TAG_PATTERN.sub("", clean)

    # 3. Strip ISO timestamp prefixes
    clean = TIMESTAMP_PATTERN.sub("", clean)

    # 4. Remove progress bar / dependency downloading noise lines
    filtered_lines: list[str] = []
    for line in clean.splitlines():
        if any(pattern.search(line) for pattern in NOISE_LINE_PATTERNS):
            continue
        filtered_lines.append(line)

    return "\n".join(filtered_lines).strip()


def extract_relevant_errors(log_text: str, max_chars: int = 6000) -> str:
    """Extract critical error stacktraces, test assertions, or compiler diagnostics.

    Filters noise first, then locates the most relevant failure segments (Python tracebacks,
    pytest failure summaries, npm/Jest errors, compiler errors). If structured failure
    blocks are found, they are extracted with context. Otherwise, returns error-matched
    lines or the tail of the log.

    Args:
        log_text: Raw or partially cleaned CI log.
        max_chars: Maximum character length for extracted snippet.

    Returns:
        Concise, highly relevant error excerpt.
    """
    cleaned = filter_ci_noise(log_text)
    if not cleaned:
        return ""

    lines = cleaned.splitlines()

    # 1. Check for Python Traceback blocks
    traceback_indices: list[int] = []
    for idx, line in enumerate(lines):
        if "Traceback (most recent call last):" in line:
            traceback_indices.append(idx)

    extracted_blocks: list[str] = []

    if traceback_indices:
        for tb_start in traceback_indices:
            # Capture from Traceback header until end of exception or blank line after error
            tb_end = min(len(lines), tb_start + 40)
            for j in range(tb_start + 1, len(lines)):
                # If we encounter another traceback or a common post-error boundary
                if j > tb_start + 2 and (
                    re.match(r"^[A-Z][a-zA-Z0-9_.]*(?:Error|Exception):", lines[j])
                    or "FAILED" in lines[j]
                ):
                    tb_end = min(len(lines), j + 2)
                    break
            extracted_blocks.append("\n".join(lines[tb_start:tb_end]))

    # 2. Check for pytest '= FAILURES =' or summary sections
    pytest_fail_idx = None
    for idx, line in enumerate(lines):
        if re.search(r"={3,}\s*FAILURES\s*={3,}", line, re.IGNORECASE) or re.search(
            r"={3,}\s*short test summary info\s*={3,}", line, re.IGNORECASE
        ):
            pytest_fail_idx = idx
            break

    if pytest_fail_idx is not None:
        fail_section = "\n".join(lines[pytest_fail_idx : min(len(lines), pytest_fail_idx + 80)])
        if fail_section not in extracted_blocks:
            extracted_blocks.append(fail_section)

    # 3. Check for specific failure/error keywords if no structured blocks found yet
    if not extracted_blocks:
        error_keywords = re.compile(
            r"\b(?:error|failed|failure|exception|fatal|panic|assertionerror|syntaxerror|typeerror)\b",
            re.IGNORECASE,
        )
        matched_indices = [idx for idx, line in enumerate(lines) if error_keywords.search(line)]

        if matched_indices:
            selected_line_indices: set[int] = set()
            for m_idx in matched_indices:
                # Add 3 lines before and 4 lines after for context
                for offset in range(-3, 5):
                    c_idx = m_idx + offset
                    if 0 <= c_idx < len(lines):
                        selected_line_indices.add(c_idx)

            sorted_indices = sorted(selected_line_indices)
            # Group into contiguous chunks
            chunks: list[list[str]] = []
            current_chunk: list[str] = []
            prev_idx = -1

            for idx in sorted_indices:
                if prev_idx != -1 and idx > prev_idx + 1:
                    chunks.append(current_chunk)
                    current_chunk = []
                current_chunk.append(lines[idx])
                prev_idx = idx

            if current_chunk:
                chunks.append(current_chunk)

            extracted_blocks.append("\n...\n".join("\n".join(chunk) for chunk in chunks[:5]))

    # 4. Fallback: if no error keywords were found, take the tail of the log
    if not extracted_blocks:
        tail_lines = lines[-60:] if len(lines) > 60 else lines
        extracted_blocks.append("\n".join(tail_lines))

    result = "\n\n---\n\n".join(extracted_blocks).strip()

    # Cap at max_chars if output is overly verbose
    if len(result) > max_chars:
        head_len = int(max_chars * 0.4)
        tail_len = int(max_chars * 0.5)
        result = (
            result[:head_len]
            + "\n\n... [Log truncated for brevity] ...\n\n"
            + result[-tail_len:]
        )

    return result


class CIWatcher:
    """Monitors remote CI checks and extracts failure diagnostics for GitHub and GitLab."""

    def __init__(
        self,
        remote_vcs: RemoteVCS | None = None,
        repo_path: Path | str = ".",
        vcs_type: VCSType | str = VCSType.AUTO,
    ) -> None:
        """Initialize CIWatcher.

        Args:
            remote_vcs: Optional RemoteVCS instance for CLI operations.
            repo_path: Root repository path.
            vcs_type: Remote VCS hosting type (github, gitlab, or auto).
        """
        self.repo_path = Path(repo_path).resolve()
        self.remote_vcs = remote_vcs or RemoteVCS(repo_path=self.repo_path, vcs_type=vcs_type)

    async def fetch_failure_logs(
        self,
        pr_number: int | None = None,
        run_id: str | int | None = None,
    ) -> str:
        """Fetch failure logs from remote CI via `gh run view --log-failed` or `glab ci trace`.

        Args:
            pr_number: Optional pull/merge request number.
            run_id: Optional specific pipeline or workflow run ID.

        Returns:
            Raw failure logs string.
        """
        try:
            vcs_type = self.remote_vcs.detect_vcs_type()
        except Exception as exc:
            logger.warning("Could not detect VCS type: %s", exc)
            vcs_type = VCSType.GITHUB

        if vcs_type == VCSType.GITHUB:
            # gh run view [<run-id>] --log-failed
            args = ["run", "view", "--log-failed"]
            if run_id is not None:
                args = ["run", "view", str(run_id), "--log-failed"]

            try:
                code, stdout, stderr = await self.remote_vcs._execute_cli("gh", *args)
                output = stdout.strip() or stderr.strip()
                if output:
                    return output
            except Exception as exc:
                logger.warning("gh run view --log-failed failed: %s", exc)

            # Fallback: try gh pr checks
            try:
                status = await self.remote_vcs.get_pipeline_status(pr_number=pr_number)
                return status.raw_output
            except Exception:
                return ""

        else:
            # glab ci trace [<job-id>]
            args = ["ci", "trace"]
            if run_id is not None:
                args = ["ci", "trace", str(run_id)]

            try:
                code, stdout, stderr = await self.remote_vcs._execute_cli("glab", *args)
                output = stdout.strip() or stderr.strip()
                if output:
                    return output
            except Exception as exc:
                logger.warning("glab ci trace failed: %s", exc)

            try:
                status = await self.remote_vcs.get_pipeline_status(pr_number=pr_number)
                return status.raw_output
            except Exception:
                return ""

    async def wait_for_pipeline(
        self,
        poll_interval: int = 15,
        timeout: int = 600,
        pr_number: int | None = None,
        branch: str | None = None,
    ) -> PipelineResult:
        """Poll remote pipeline status until completion, failure, or timeout.

        Args:
            poll_interval: Seconds to wait between polling attempts.
            timeout: Maximum allowed time in seconds to wait for completion.
            pr_number: Optional PR/MR number to inspect.
            branch: Optional branch name.

        Returns:
            PipelineResult model with state, pass/fail boolean, and extracted error logs.
        """
        start_time = asyncio.get_event_loop().time()
        logger.info(
            "Waiting for CI pipeline (poll_interval=%ds, timeout=%ds, pr=%s, branch=%s)",
            poll_interval,
            timeout,
            pr_number,
            branch,
        )

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time

            try:
                pipeline_status = await self.remote_vcs.get_pipeline_status(pr_number=pr_number)
            except Exception as exc:
                logger.warning("Error querying pipeline status: %s", exc)
                pipeline_status = PipelineStatus(
                    state=PipelineState.UNKNOWN,
                    all_passed=False,
                    raw_output=str(exc),
                )

            state = pipeline_status.state
            if isinstance(state, str):
                try:
                    state = PipelineState(state)
                except ValueError:
                    state = PipelineState.UNKNOWN

            logger.info("Pipeline poll check: state=%s, elapsed=%.1fs", state, elapsed)

            if state == PipelineState.SUCCESS or pipeline_status.all_passed:
                logger.info("Pipeline succeeded successfully after %.1fs.", elapsed)
                return PipelineResult(
                    status=PipelineState.SUCCESS,
                    passed=True,
                    error_logs="",
                    raw_logs=pipeline_status.raw_output,
                    checks=pipeline_status.checks,
                    details_url=pipeline_status.url,
                    duration_seconds=elapsed,
                )

            if state == PipelineState.FAILED:
                logger.warning("Pipeline failed after %.1fs. Extracting error logs...", elapsed)
                raw_logs = await self.fetch_failure_logs(pr_number=pr_number)
                if not raw_logs:
                    raw_logs = pipeline_status.raw_output

                error_logs = extract_relevant_errors(raw_logs)
                return PipelineResult(
                    status=PipelineState.FAILED,
                    passed=False,
                    error_logs=error_logs,
                    raw_logs=raw_logs,
                    checks=pipeline_status.checks,
                    details_url=pipeline_status.url,
                    duration_seconds=elapsed,
                )

            # Check for timeout
            if elapsed >= timeout:
                logger.error("Pipeline monitoring timed out after %.1fs (state=%s).", elapsed, state)
                raw_logs = await self.fetch_failure_logs(pr_number=pr_number)
                if not raw_logs:
                    raw_logs = pipeline_status.raw_output

                extracted = extract_relevant_errors(raw_logs)
                timeout_msg = f"Pipeline monitoring timed out after {timeout} seconds (state: {state})."
                error_logs = f"{timeout_msg}\n\n{extracted}".strip() if extracted else timeout_msg
                return PipelineResult(
                    status=PipelineState.FAILED,
                    passed=False,
                    error_logs=error_logs,
                    raw_logs=raw_logs,
                    checks=pipeline_status.checks,
                    details_url=pipeline_status.url,
                    duration_seconds=elapsed,
                )

            # Still pending or running - sleep until next check
            sleep_time = min(float(poll_interval), max(0.1, timeout - elapsed))
            await asyncio.sleep(sleep_time)


# Module-level convenience function
async def wait_for_pipeline(
    poll_interval: int = 15,
    timeout: int = 600,
    pr_number: int | None = None,
    branch: str | None = None,
    remote_vcs: RemoteVCS | None = None,
    repo_path: Path | str = ".",
    vcs_type: str = "auto",
) -> PipelineResult:
    """Wait for remote CI pipeline and return structured result with error extraction."""
    watcher = CIWatcher(
        remote_vcs=remote_vcs,
        repo_path=repo_path,
        vcs_type=vcs_type,
    )
    return await watcher.wait_for_pipeline(
        poll_interval=poll_interval,
        timeout=timeout,
        pr_number=pr_number,
        branch=branch,
    )
