"""Tests for CI pipeline detection and user intervention / graceful cancellation."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.intervention import (
    InterventionAction,
    OrchestrationCancelledError,
    OrchestrationInterruptedError,
    parse_intervention_command,
    prompt_user_intervention,
)
from core.models import PipelineState, WorkflowPhase, WorkflowState
from vcs.ci_watcher import CIWatcher, PipelineResult
from vcs.remote_vcs import RemoteVCS, VCSType, has_ci_configuration


def test_parse_intervention_command():
    assert parse_intervention_command("s") == InterventionAction.SKIP
    assert parse_intervention_command("skip") == InterventionAction.SKIP
    assert parse_intervention_command("weiter") == InterventionAction.SKIP
    assert parse_intervention_command("überspringen") == InterventionAction.SKIP

    assert parse_intervention_command("p") == InterventionAction.PAUSE
    assert parse_intervention_command("pause") == InterventionAction.PAUSE

    assert parse_intervention_command("a") == InterventionAction.ABORT
    assert parse_intervention_command("abort") == InterventionAction.ABORT
    assert parse_intervention_command("cancel") == InterventionAction.ABORT
    assert parse_intervention_command("abbrechen") == InterventionAction.ABORT

    assert parse_intervention_command("c") == InterventionAction.CONTINUE
    assert parse_intervention_command("continue") == InterventionAction.CONTINUE

    assert parse_intervention_command("r") == InterventionAction.RETRY
    assert parse_intervention_command("retry") == InterventionAction.RETRY


def test_prompt_user_intervention_custom_input():
    action = prompt_user_intervention(input_func=lambda _: "skip")
    assert action == InterventionAction.SKIP

    action_abort = prompt_user_intervention(input_func=lambda _: "abbrechen")
    assert action_abort == InterventionAction.ABORT


def test_has_ci_configuration(tmp_path: Path):
    # Empty repo has no CI
    assert has_ci_configuration(tmp_path) is False

    # Adding .github/workflows/ci.yml
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text("name: CI\n")
    assert has_ci_configuration(tmp_path) is True


@pytest.mark.asyncio
async def test_wait_for_pipeline_no_pipeline_immediate(tmp_path: Path):
    async def mock_runner(binary, *args):
        return 1, "", "no checks reported on the 'main' branch"

    remote_vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner, repo_path=tmp_path)
    watcher = CIWatcher(remote_vcs=remote_vcs, repo_path=tmp_path)

    result = await watcher.wait_for_pipeline(poll_interval=0.1, timeout=5)
    assert result.passed is True
    assert result.status == PipelineState.NO_PIPELINE
    assert result.error_logs == ""


@pytest.mark.asyncio
async def test_wait_for_pipeline_user_skip_intervention(tmp_path: Path):
    async def mock_runner(binary, *args):
        return 0, "*  test   in_progress   10s   https://runs/1\n", ""

    remote_vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner, repo_path=tmp_path)
    watcher = CIWatcher(remote_vcs=remote_vcs, repo_path=tmp_path)

    with patch("asyncio.sleep", side_effect=[KeyboardInterrupt()]):
        result = await watcher.wait_for_pipeline(
            poll_interval=1,
            timeout=10,
            input_func=lambda _: "skip",
        )
        assert result.passed is True
        assert result.status == PipelineState.NO_PIPELINE


@pytest.mark.asyncio
async def test_wait_for_pipeline_user_abort_intervention(tmp_path: Path):
    async def mock_runner(binary, *args):
        return 0, "*  test   in_progress   10s   https://runs/1\n", ""

    remote_vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner, repo_path=tmp_path)
    watcher = CIWatcher(remote_vcs=remote_vcs, repo_path=tmp_path)

    with patch("asyncio.sleep", side_effect=[KeyboardInterrupt()]):
        with pytest.raises(OrchestrationCancelledError):
            await watcher.wait_for_pipeline(
                poll_interval=1,
                timeout=10,
                input_func=lambda _: "abort",
            )
