"""Comprehensive tests for Remote VCS wrapper (GitHub `gh` and GitLab `glab`)."""

from unittest.mock import patch

import pytest

from core.models import PipelineState
from vcs.remote_vcs import (
    RemoteVCS,
    RemoteVCSError,
    VCSType,
    create_draft_pr,
    get_pipeline_status,
)


# ===========================================================================
# 1. VCS DETECTION TESTS
# ===========================================================================

def test_detect_vcs_type_explicit():
    vcs_gh = RemoteVCS(vcs_type=VCSType.GITHUB)
    assert vcs_gh.detect_vcs_type() == VCSType.GITHUB

    vcs_glab = RemoteVCS(vcs_type=VCSType.GITLAB)
    assert vcs_glab.detect_vcs_type() == VCSType.GITLAB


@patch("shutil.which")
def test_detect_vcs_type_auto_github(mock_which):
    mock_which.side_effect = lambda binary: "C:\\bin\\gh.exe" if binary == "gh" else None
    vcs = RemoteVCS(vcs_type=VCSType.AUTO)
    assert vcs.detect_vcs_type() == VCSType.GITHUB


@patch("shutil.which")
def test_detect_vcs_type_auto_gitlab(mock_which):
    mock_which.side_effect = lambda binary: "C:\\bin\\glab.exe" if binary == "glab" else None
    vcs = RemoteVCS(vcs_type=VCSType.AUTO)
    assert vcs.detect_vcs_type() == VCSType.GITLAB


@patch("shutil.which")
def test_detect_vcs_type_auto_none(mock_which):
    mock_which.return_value = None
    vcs = RemoteVCS(vcs_type=VCSType.AUTO)
    with pytest.raises(RemoteVCSError) as exc_info:
        vcs.detect_vcs_type()
    assert "Neither 'gh'" in str(exc_info.value)


# ===========================================================================
# 2. DRAFT PR / MR CREATION TESTS
# ===========================================================================

@pytest.mark.asyncio
async def test_create_draft_pr_github():
    async def mock_runner(binary, *args):
        assert binary == "gh"
        assert "--draft" in args
        assert "--title" in args
        assert "--head" in args
        return 0, "https://github.com/org/repo/pull/42\n", ""

    vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    url = await vcs.create_draft_pr(
        title="feat: implement login",
        body="## Details\nTesting PR creation.",
        branch="feat/SPEC-login",
    )
    assert url == "https://github.com/org/repo/pull/42"


@pytest.mark.asyncio
async def test_create_draft_pr_gitlab():
    async def mock_runner(binary, *args):
        assert binary == "glab"
        assert "-d" in args
        assert "--source-branch" in args
        return 0, "https://gitlab.com/org/repo/-/merge_requests/99\n", ""

    vcs = RemoteVCS(vcs_type=VCSType.GITLAB, runner=mock_runner)
    url = await vcs.create_draft_pr(
        title="feat: gitlab feature",
        body="## GitLab description",
        branch="feat/SPEC-glab",
    )
    assert url == "https://gitlab.com/org/repo/-/merge_requests/99"


@pytest.mark.asyncio
async def test_create_draft_pr_failure():
    async def failing_runner(binary, *args):
        return 1, "", "fatal: not logged in"

    vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=failing_runner)
    with pytest.raises(RemoteVCSError) as exc_info:
        await vcs.create_draft_pr("title", "body", "feat/branch")
    assert "GitHub PR creation failed" in str(exc_info.value)


# ===========================================================================
# 3. PIPELINE STATUS INSPECTION TESTS
# ===========================================================================

@pytest.mark.asyncio
async def test_get_pipeline_status_github_success():
    gh_output = (
        "✓  build (ubuntu-latest)     pass  1m12s  https://github.com/org/repo/actions/runs/1\n"
        "✓  test (pytest)             pass  45s    https://github.com/org/repo/actions/runs/2\n"
        "✓  lint (ruff)               pass  10s    https://github.com/org/repo/actions/runs/3\n"
    )

    async def mock_runner(binary, *args):
        assert binary == "gh"
        assert args == ("pr", "checks", "42")
        return 0, gh_output, ""

    vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    status = await vcs.get_pipeline_status(pr_number=42)

    assert status.state == PipelineState.SUCCESS
    assert status.all_passed is True
    assert len(status.checks) == 3
    assert status.checks[0].conclusion == "success"
    assert status.checks[0].details_url == "https://github.com/org/repo/actions/runs/1"


@pytest.mark.asyncio
async def test_get_pipeline_status_github_failure():
    gh_output = (
        "✓  build                     pass  1m12s  https://github.com/runs/1\n"
        "X  test (pytest)             fail  45s    https://github.com/runs/2\n"
    )

    async def mock_runner(binary, *args):
        return 0, gh_output, ""

    vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    status = await vcs.get_pipeline_status()

    assert status.state == PipelineState.FAILED
    assert status.all_passed is False
    assert len(status.checks) == 2
    assert status.checks[1].conclusion == "failure"


@pytest.mark.asyncio
async def test_get_pipeline_status_github_pending():
    gh_output = (
        "✓  build                     pass     1m12s  https://github.com/runs/1\n"
        "*  test (pytest)             pending  -      https://github.com/runs/2\n"
    )

    async def mock_runner(binary, *args):
        return 0, gh_output, ""

    vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    status = await vcs.get_pipeline_status()

    assert status.state == PipelineState.RUNNING
    assert status.all_passed is False
    assert status.checks[1].status == "in_progress"


@pytest.mark.asyncio
async def test_get_pipeline_status_gitlab():
    async def mock_runner(binary, *args):
        assert binary == "glab"
        return 0, "Pipeline #12345: passed\n", ""

    vcs = RemoteVCS(vcs_type=VCSType.GITLAB, runner=mock_runner)
    status = await vcs.get_pipeline_status(pr_number=10)

    assert status.state == PipelineState.SUCCESS
    assert status.all_passed is True


@pytest.mark.asyncio
async def test_get_pipeline_status_command_failure():
    async def mock_runner(binary, *args):
        return 1, "", "No checks reported"

    vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    status = await vcs.get_pipeline_status()

    assert status.state == PipelineState.UNKNOWN
    assert status.all_passed is False
    assert status.raw_output == "No checks reported"


# ===========================================================================
# 4. PR / MR MERGE TESTS
# ===========================================================================

@pytest.mark.asyncio
async def test_merge_pr_github_auto_squash():
    recorded_commands = []

    async def mock_runner(binary, *args):
        recorded_commands.append((binary, list(args)))
        return 0, "https://github.com/org/repo/pull/42 merged\n", ""

    vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    code, stdout, stderr = await vcs.merge_pr(pr_number=42, auto=True, squash=True)

    assert code == 0
    assert "merged" in stdout
    assert recorded_commands == [("gh", ["pr", "merge", "42", "--auto", "--squash"])]


@pytest.mark.asyncio
async def test_merge_pr_gitlab_manual_squash():
    recorded_commands = []

    async def mock_runner(binary, *args):
        recorded_commands.append((binary, list(args)))
        return 0, "Merge request !99 merged\n", ""

    vcs = RemoteVCS(vcs_type=VCSType.GITLAB, runner=mock_runner)
    code, stdout, stderr = await vcs.merge_pr(pr_number=99, auto=False, squash=True)

    assert code == 0
    assert "merged" in stdout
    assert recorded_commands == [("glab", ["mr", "merge", "99", "--squash"])]


@pytest.mark.asyncio
async def test_merge_pr_failure_raises_error():
    async def mock_runner(binary, *args):
        return 1, "", "Cannot merge: branch is out of date"

    vcs = RemoteVCS(vcs_type=VCSType.GITHUB, runner=mock_runner)
    with pytest.raises(RemoteVCSError) as exc_info:
        await vcs.merge_pr(pr_number=10)
    assert "GitHub PR merge failed" in str(exc_info.value)

