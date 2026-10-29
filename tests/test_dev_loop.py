"""Comprehensive tests for Dev Agent Loop Controller (Phase 4)."""

import asyncio
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.dev_loop import (
    DevActionType,
    DevAgentStep,
    DevLoopError,
    extract_spec_slug,
    extract_spec_title,
    load_developer_prompt,
    run_dev_phase,
)
from core.models import WorkflowPhase, WorkflowState
from core.provider import BaseLLMProvider
from security.gatekeeper import Gatekeeper
from vcs.git_handler import GitHandler
from vcs.remote_vcs import RemoteVCS, VCSType


@pytest.fixture
def repo_sandbox(tmp_path: Path) -> Path:
    """Creates a temporary git repository configured for dev loop testing."""
    repo = tmp_path / "sandbox_repo"
    repo.mkdir()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "DevAgent Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@mergeflow.ai"], cwd=repo, check=True, capture_output=True)

    (repo / "README.md").write_text("# Sandbox Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo, check=True, capture_output=True)

    return repo


# ===========================================================================
# 1. SPEC METADATA EXTRACTION & PROMPT LOADING
# ===========================================================================

def test_extract_spec_metadata():
    spec_md = """# SPEC: OAuth2 User Authentication
## 1. Overview
Implement Google and GitHub OAuth2 login.
"""
    assert extract_spec_slug(spec_md) == "oauth2-user-authentication"
    assert extract_spec_title(spec_md) == "OAuth2 User Authentication"

    fallback_md = "Just raw markdown without spec header."
    assert extract_spec_slug(fallback_md, "TASK-42") == "task-42"
    assert extract_spec_title(fallback_md) == "Feature Implementation"


def test_load_developer_prompt():
    prompt = load_developer_prompt()
    assert "MergeFlow Autonomous Developer Agent" in prompt
    assert "Conventional Commits" in prompt
    assert "Gatekeeper" in prompt


# ===========================================================================
# 2. DEV AGENT FULL WORKFLOW RUN
# ===========================================================================

@pytest.mark.asyncio
async def test_run_dev_phase_successful_flow(repo_sandbox: Path):
    spec = """# SPEC: Calculator Module
## 1. Overview & Business Goal
Provide a simple calculator with add and subtract functions.

## 4. Acceptance Criteria
- [ ] add(a, b) returns a + b
"""
    state = WorkflowState(
        task_id="calc-101",
        raw_requirements="Implement a calculator module with unit tests.",
    )
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    git = GitHandler(repo_path=repo_sandbox)

    # Mock remote VCS runner
    async def mock_vcs_runner(binary, *args):
        return 0, "https://github.com/test-org/test-repo/pull/101\n", ""

    remote_vcs = RemoteVCS(repo_path=repo_sandbox, vcs_type=VCSType.GITHUB, runner=mock_vcs_runner)

    # Mock LLM provider emitting sequential actions
    mock_provider = AsyncMock(spec=BaseLLMProvider)
    mock_provider.complete.side_effect = [
        # Turn 1: Write implementation file
        DevAgentStep(
            thought="Write calculator implementation.",
            action_type=DevActionType.WRITE_FILE,
            file_path="calc.py",
            file_content="def add(a, b): return a + b\n",
        ),
        # Turn 2: Run test inspection command
        DevAgentStep(
            thought="Verify status.",
            action_type=DevActionType.RUN_COMMAND,
            command="git status",
        ),
        # Turn 3: Mark complete
        DevAgentStep(
            thought="Implementation verified.",
            action_type=DevActionType.COMPLETE,
            tests_passed=True,
        ),
    ]

    # Mock git push to avoid needing an external remote
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(git, "push_branch", AsyncMock(return_value="Everything up-to-date"))

        await run_dev_phase(
            spec=spec,
            state=state,
            gatekeeper=gatekeeper,
            provider=mock_provider,
            git_handler=git,
            remote_vcs=remote_vcs,
            max_iterations=5,
        )

    # Assertions
    assert state.branch_name == "feat/SPEC-calculator-module"
    assert state.current_phase == WorkflowPhase.CI_LOOP.value
    assert state.metadata.get("pr_url") == "https://github.com/test-org/test-repo/pull/101"

    # Verify file was written to disk
    assert (repo_sandbox / "calc.py").is_file()
    assert "def add" in (repo_sandbox / "calc.py").read_text(encoding="utf-8")

    # Verify commit was created on feature branch
    diff = await git.get_diff("main")
    assert "calc.py" in diff


# ===========================================================================
# 3. DEV AGENT GATEKEEPER INTERCEPTION & SECURITY
# ===========================================================================

@pytest.mark.asyncio
async def test_run_dev_phase_gatekeeper_blocks_malicious_command(repo_sandbox: Path):
    spec = "# SPEC: Security Check\nDangerous command test."
    state = WorkflowState(task_id="sec-1", raw_requirements="test")
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    git = GitHandler(repo_path=repo_sandbox)

    async def mock_vcs_runner(binary, *args):
        return 0, "https://github.com/org/repo/pull/1", ""

    remote_vcs = RemoteVCS(repo_path=repo_sandbox, vcs_type=VCSType.GITHUB, runner=mock_vcs_runner)

    mock_provider = AsyncMock(spec=BaseLLMProvider)
    mock_provider.complete.side_effect = [
        # Attempt destructive command
        DevAgentStep(
            thought="Try running forbidden command.",
            action_type=DevActionType.RUN_COMMAND,
            command="rm -rf /",
        ),
        # Agent realizes failure, completes
        DevAgentStep(
            thought="Acknowledge block and complete.",
            action_type=DevActionType.COMPLETE,
            tests_passed=True,
        ),
    ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(git, "push_branch", AsyncMock(return_value="OK"))

        await run_dev_phase(
            spec=spec,
            state=state,
            gatekeeper=gatekeeper,
            provider=mock_provider,
            git_handler=git,
            remote_vcs=remote_vcs,
            max_iterations=3,
        )

    # Verify phase transitioned normally after agent corrected course
    assert state.current_phase == WorkflowPhase.CI_LOOP.value


@pytest.mark.asyncio
async def test_run_dev_phase_gatekeeper_blocks_forbidden_file_write(repo_sandbox: Path):
    spec = "# SPEC: Env Leak Check\nWrite to .env"
    state = WorkflowState(task_id="sec-2", raw_requirements="test")
    gatekeeper = Gatekeeper(repo_root=repo_sandbox)
    git = GitHandler(repo_path=repo_sandbox)

    mock_provider = AsyncMock(spec=BaseLLMProvider)
    mock_provider.complete.side_effect = [
        # Attempt to write to .env
        DevAgentStep(
            thought="Write sensitive file.",
            action_type=DevActionType.WRITE_FILE,
            file_path=".env",
            file_content="SECRET=12345\n",
        ),
        # Complete
        DevAgentStep(
            thought="Finish",
            action_type=DevActionType.COMPLETE,
            tests_passed=True,
        ),
    ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(git, "push_branch", AsyncMock(return_value="OK"))

        await run_dev_phase(
            spec=spec,
            state=state,
            gatekeeper=gatekeeper,
            provider=mock_provider,
            git_handler=git,
            remote_vcs=RemoteVCS(repo_path=repo_sandbox, runner=AsyncMock(return_value=(0, "http://pr", ""))),
            max_iterations=3,
        )

    # Verify .env was NOT created
    assert not (repo_sandbox / ".env").exists()
