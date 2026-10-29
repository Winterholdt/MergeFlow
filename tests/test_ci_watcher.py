"""Comprehensive tests for CI-Watcher and log extraction engine (Phase 5)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.models import PipelineCheck, PipelineState, PipelineStatus
from vcs.ci_watcher import (
    CIWatcher,
    PipelineResult,
    extract_relevant_errors,
    filter_ci_noise,
    wait_for_pipeline,
)
from vcs.remote_vcs import RemoteVCS, VCSType


# ===========================================================================
# 1. NOISE FILTERING & LOG CLEANING TESTS
# ===========================================================================

def test_filter_ci_noise_ansi_and_runner_tags():
    raw_log = (
        "\x1b[31m\x1b[1mFAILED\x1b[0m tests/test_calc.py::test_add\n"
        "##[group]Run tests\n"
        "##[debug]Runner version 2.300.0\n"
        "2026-10-29T16:50:00.1234567Z AssertionError: 4 != 5\n"
        "##[endgroup]\n"
    )
    cleaned = filter_ci_noise(raw_log)
    assert "\x1b[" not in cleaned
    assert "##[" not in cleaned
    assert "2026-10-29T16:50:00" not in cleaned
    assert "FAILED tests/test_calc.py::test_add" in cleaned
    assert "AssertionError: 4 != 5" in cleaned


def test_filter_ci_noise_download_and_progress_bars():
    raw_log = (
        "Downloading package-1.0.0-py3-none-any.whl (1.2 MB)\n"
        "Requirement already satisfied: pydantic in /site-packages\n"
        "Collecting pytest\n"
        "| ##### | 50%\n"
        "CACHED [1/4]\n"
        "Actual important build line\n"
    )
    cleaned = filter_ci_noise(raw_log)
    assert "Downloading package" not in cleaned
    assert "Requirement already satisfied" not in cleaned
    assert "Collecting pytest" not in cleaned
    assert "50%" not in cleaned
    assert "CACHED" not in cleaned
    assert "Actual important build line" in cleaned


# ===========================================================================
# 2. ERROR EXTRACTION TESTS
# ===========================================================================

def test_extract_python_traceback():
    raw_log = """
2026-10-29T12:00:00.000Z Starting test suite
Traceback (most recent call last):
  File "mergeflow/calc.py", line 12, in add
    return a / b
ZeroDivisionError: division by zero
2026-10-29T12:00:05.000Z Test run completed with errors.
"""
    extracted = extract_relevant_errors(raw_log)
    assert "Traceback (most recent call last):" in extracted
    assert 'File "mergeflow/calc.py", line 12' in extracted
    assert "ZeroDivisionError: division by zero" in extracted
    assert "Starting test suite" not in extracted


def test_extract_pytest_failures_section():
    raw_log = """
rootdir: /repo
collected 10 items
.........F
=================================== FAILURES ===================================
__________________________________ test_auth ___________________________________
tests/test_auth.py:42: in test_auth
    assert user.is_authenticated is True
E   AssertionError: assert False is True
=========================== short test summary info ===========================
FAILED tests/test_auth.py::test_auth - AssertionError: assert False is True
========================= 1 failed, 9 passed in 0.45s ==========================
"""
    extracted = extract_relevant_errors(raw_log)
    assert "FAILURES" in extracted
    assert "AssertionError: assert False is True" in extracted
    assert "FAILED tests/test_auth.py::test_auth" in extracted


def test_extract_generic_error_context():
    raw_log = """
Compiling modules...
Building target release...
fatal: could not read Username for 'https://github.com': No such device or address
Post-cleanup operations...
Done.
"""
    extracted = extract_relevant_errors(raw_log)
    assert "fatal: could not read Username" in extracted


def test_extract_max_chars_truncation():
    # Large log with repetitive traceback
    large_log = "Traceback (most recent call last):\n" + ("  File 'test.py', line 1\n    x = 1\n" * 500) + "ValueError: too large\n"
    extracted = extract_relevant_errors(large_log, max_chars=500)
    assert len(extracted) <= 700
    assert "[Log truncated for brevity]" in extracted


# ===========================================================================
# 3. CI WATCHER FAILURE LOG EXTRACTION (GH / GLAB)
# ===========================================================================

@pytest.mark.asyncio
async def test_fetch_failure_logs_github():
    async def mock_runner(binary, *args):
        assert binary == "gh"
        assert "run" in args
        assert "view" in args
        assert "--log-failed" in args
        return 0, "GitHub Action Failed Log Content\nTraceback...", ""

    remote_vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    watcher = CIWatcher(remote_vcs=remote_vcs)

    logs = await watcher.fetch_failure_logs(run_id=12345)
    assert "GitHub Action Failed Log Content" in logs


@pytest.mark.asyncio
async def test_fetch_failure_logs_gitlab():
    async def mock_runner(binary, *args):
        assert binary == "glab"
        assert "ci" in args
        assert "trace" in args
        return 0, "GitLab CI Trace Failed Output\nError: build failed", ""

    remote_vcs = RemoteVCS(vcs_type=VCSType.GITLAB, runner=mock_runner)
    watcher = CIWatcher(remote_vcs=remote_vcs)

    logs = await watcher.fetch_failure_logs(run_id="job-99")
    assert "GitLab CI Trace Failed Output" in logs


# ===========================================================================
# 4. WAIT FOR PIPELINE POLLING TESTS
# ===========================================================================

@pytest.mark.asyncio
async def test_wait_for_pipeline_immediate_success():
    async def mock_status_runner(binary, *args):
        return 0, "✓  build   pass   1m 20s   https://github.com/org/repo/runs/1\n", ""

    remote_vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_status_runner)
    watcher = CIWatcher(remote_vcs=remote_vcs)

    result = await watcher.wait_for_pipeline(poll_interval=1, timeout=10)
    assert result.passed is True
    assert result.status == PipelineState.SUCCESS
    assert result.error_logs == ""
    assert len(result.checks) == 1


@pytest.mark.asyncio
async def test_wait_for_pipeline_pending_then_success():
    call_count = 0

    async def mock_runner(binary, *args):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First poll: in progress
            return 0, "*  test   in_progress   20s   https://github.com/runs/1\n", ""
        # Second poll: success
        return 0, "✓  test   pass   45s   https://github.com/runs/1\n", ""

    remote_vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    watcher = CIWatcher(remote_vcs=remote_vcs)

    result = await watcher.wait_for_pipeline(poll_interval=0.05, timeout=5)
    assert result.passed is True
    assert result.status == PipelineState.SUCCESS
    assert call_count >= 2


@pytest.mark.asyncio
async def test_wait_for_pipeline_failed_extracts_logs():
    async def mock_runner(binary, *args):
        if "checks" in args:
            # gh pr checks returns failure
            return 0, "X  test   fail   30s   https://github.com/runs/10\n", ""
        elif "view" in args:
            # gh run view --log-failed
            return 0, "Traceback (most recent call last):\n  File 'a.py', line 1\nAssertionError: failed!\n", ""
        return 0, "", ""

    remote_vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    watcher = CIWatcher(remote_vcs=remote_vcs)

    result = await watcher.wait_for_pipeline(poll_interval=1, timeout=5)
    assert result.passed is False
    assert result.status == PipelineState.FAILED
    assert "AssertionError: failed!" in result.error_logs


@pytest.mark.asyncio
async def test_wait_for_pipeline_timeout():
    async def mock_runner(binary, *args):
        # Always running
        return 0, "*  test   in_progress   10m   https://github.com/runs/1\n", ""

    remote_vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    watcher = CIWatcher(remote_vcs=remote_vcs)

    result = await watcher.wait_for_pipeline(poll_interval=0.05, timeout=0.1)
    assert result.passed is False
    assert result.status == PipelineState.FAILED
    assert "timed out" in result.error_logs


@pytest.mark.asyncio
async def test_module_wait_for_pipeline_convenience():
    async def mock_runner(binary, *args):
        return 0, "✓  lint   pass   10s   https://github.com/runs/1\n", ""

    remote_vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    result = await wait_for_pipeline(
        poll_interval=0.1,
        timeout=2,
        remote_vcs=remote_vcs,
    )
    assert result.passed is True
    assert result.status == PipelineState.SUCCESS
