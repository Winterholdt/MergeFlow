"""Comprehensive tests for GitHandler and local Git operations (Phase 4)."""

import asyncio
from pathlib import Path
import subprocess

import pytest

from vcs.git_handler import (
    GitCommandError,
    GitError,
    GitHandler,
    commit_changes,
    create_feature_branch,
    ensure_conventional_commit,
    get_diff,
    is_conventional_commit,
    push_branch,
    get_recent_commits,
    sanitize_slug,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initializes a real git repository in a temporary directory for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Configure local git repository
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "MergeFlow Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@mergeflow.ai"], cwd=repo, check=True, capture_output=True)

    # Initial commit on main
    readme = repo / "README.md"
    readme.write_text("# Main Branch\nInitial content.", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo, check=True, capture_output=True)

    return repo


# ===========================================================================
# 1. SLUG SANITIZATION TESTS
# ===========================================================================

@pytest.mark.parametrize(
    "raw_input,expected_slug",
    [
        ("User Authentication & OAuth2", "user-authentication-oauth2"),
        ("SPEC: Implement Payment Gateway!", "spec-implement-payment-gateway"),
        ("  spaces   and---dashes  ", "spaces-and-dashes"),
        ("FEATURE_123_test", "feature_123_test"),
        ("$$$Special&&&Chars###", "special-chars"),
        ("", "task"),
    ],
)
def test_sanitize_slug(raw_input: str, expected_slug: str):
    assert sanitize_slug(raw_input) == expected_slug


# ===========================================================================
# 2. CONVENTIONAL COMMITS VALIDATION TESTS
# ===========================================================================

@pytest.mark.parametrize(
    "message,is_valid",
    [
        ("feat: add login endpoint", True),
        ("fix(auth): resolve token expiration bug", True),
        ("docs: update architecture diagram", True),
        ("style: format with ruff", True),
        ("refactor: extract shell execution into runner", True),
        ("perf: optimize query indexes", True),
        ("test: add tests for git handler", True),
        ("build: update pyproject dependencies", True),
        ("ci: configure github actions pipeline", True),
        ("chore: clean up scratch files", True),
        ("revert: undo previous commit", True),
        ("feat!: breaking change in provider API", True),
        ("fix(core)!: major breaking fix", True),
        ("random commit message without type", False),
        ("added new feature", False),
        ("WIP: working on stuff", False),
    ],
)
def test_is_conventional_commit(message: str, is_valid: bool):
    assert is_conventional_commit(message) is is_valid


def test_ensure_conventional_commit():
    assert ensure_conventional_commit("feat: valid message") == "feat: valid message"
    assert ensure_conventional_commit("fix(api): fix null pointer") == "fix(api): fix null pointer"
    assert ensure_conventional_commit("add login support") == "feat: add login support"
    assert ensure_conventional_commit("fix bug in loop", default_type="fix") == "fix: fix bug in loop"
    assert ensure_conventional_commit("") == "feat: update codebase"


# ===========================================================================
# 3. GITHANDLER BRANCH OPERATIONS
# ===========================================================================

@pytest.mark.asyncio
async def test_create_feature_branch(git_repo: Path):
    handler = GitHandler(repo_path=git_repo)

    # 1. Create new feature branch
    branch_name = await handler.create_feature_branch("user-auth")
    assert branch_name == "feat/SPEC-user-auth"

    current = await handler.get_current_branch()
    assert current == "feat/SPEC-user-auth"
    assert await handler.branch_exists("feat/SPEC-user-auth")

    # 2. Checkout existing branch without error
    same_branch = await handler.create_feature_branch("user-auth")
    assert same_branch == "feat/SPEC-user-auth"
    assert await handler.get_current_branch() == "feat/SPEC-user-auth"


@pytest.mark.asyncio
async def test_create_feature_branch_slug_variants(git_repo: Path):
    handler = GitHandler(repo_path=git_repo)

    # Slug already with feat/
    b1 = await handler.create_feature_branch("feat/custom-branch")
    assert b1 == "feat/custom-branch"

    # Slug with spec-
    b2 = await handler.create_feature_branch("spec-api-refactor")
    assert b2 == "feat/spec-api-refactor"


# ===========================================================================
# 4. GITHANDLER COMMIT & DIFF OPERATIONS
# ===========================================================================

@pytest.mark.asyncio
async def test_commit_changes(git_repo: Path):
    handler = GitHandler(repo_path=git_repo)
    await handler.create_feature_branch("feature-test")

    # Modify an existing file and create a new one
    (git_repo / "new_file.py").write_text("def hello(): return 'world'\n", encoding="utf-8")
    assert await handler.has_uncommitted_changes()

    commit_hash = await handler.commit_changes("add new python file")
    assert commit_hash
    assert len(commit_hash) >= 7

    # Working tree should be clean now
    assert not await handler.has_uncommitted_changes()

    # Commit again with no changes returns current HEAD hash without error
    head_hash = await handler.commit_changes("chore: empty commit")
    assert head_hash == commit_hash


@pytest.mark.asyncio
async def test_get_diff(git_repo: Path):
    handler = GitHandler(repo_path=git_repo)
    await handler.create_feature_branch("diff-check")

    (git_repo / "feature.txt").write_text("Hello from feature branch!\n", encoding="utf-8")
    await handler.commit_changes("feat: add feature text")

    diff = await handler.get_diff("main")
    assert "Hello from feature branch!" in diff
    assert "feature.txt" in diff


@pytest.mark.asyncio
async def test_push_branch_mocked(git_repo: Path):
    handler = GitHandler(repo_path=git_repo)
    await handler.create_feature_branch("push-test")

    # push without remote will fail in git subprocess, raising GitCommandError
    with pytest.raises(GitCommandError) as exc_info:
        await handler.push_branch(remote="nonexistent-remote")
    assert "push" in exc_info.value.cmd


# ===========================================================================
# 5. MODULE-LEVEL FUNCTIONS
# ===========================================================================

@pytest.mark.asyncio
async def test_module_convenience_functions(git_repo: Path):
    branch = await create_feature_branch("quick-test", repo_path=git_repo)
    assert branch == "feat/SPEC-quick-test"

    (git_repo / "quick.txt").write_text("quick content", encoding="utf-8")
    commit_hash = await commit_changes("feat: quick commit", repo_path=git_repo)
    assert commit_hash

    diff = await get_diff("main", repo_path=git_repo)
    assert "quick.txt" in diff

    commits = await get_recent_commits(count=5, repo_path=git_repo)
    assert "quick commit" in commits
