"""Local Git operations engine for MergeFlow orchestrator.

Provides asynchronous Git operations using native asyncio subprocesses:
- Feature branch creation and switching (feat/SPEC-<slug>)
- Diff computation against base branch
- Conventional Commits staging and committing
- Push operations with upstream tracking
"""

import asyncio
import logging
from pathlib import Path
import re
import shutil

logger = logging.getLogger(__name__)

CONVENTIONAL_COMMIT_PATTERN = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\([a-zA-Z0-9_.\-]+\))?!?: .+$",
    re.DOTALL,
)


class GitError(Exception):
    """Base exception for Git operations."""


class GitCommandError(GitError):
    """Raised when a git command returns a non-zero exit code."""

    def __init__(
        self,
        cmd: str,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(f"Command '{cmd}' failed with exit code {returncode}: {stderr.strip() or stdout.strip()}")
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def sanitize_slug(slug: str) -> str:
    """Sanitize a raw text string into a clean git branch slug.

    Args:
        slug: Input text (e.g. task title, issue name).

    Returns:
        URL/git-safe hyphenated slug in lowercase.
    """
    clean = slug.strip().lower()
    # Replace non-alphanumeric characters (except hyphens and underscores) with hyphens
    clean = re.sub(r"[^a-z0-9_\-]+", "-", clean)
    # Collapse consecutive hyphens
    clean = re.sub(r"-+", "-", clean)
    # Strip leading/trailing hyphens or underscores
    clean = clean.strip("-_")
    return clean or "task"


def is_conventional_commit(message: str) -> bool:
    """Check whether a commit message adheres to Conventional Commits specification.

    Args:
        message: The commit message to inspect.

    Returns:
        True if the message follows Conventional Commits format, False otherwise.
    """
    first_line = message.strip().split("\n", 1)[0].strip()
    return bool(CONVENTIONAL_COMMIT_PATTERN.match(first_line))


def ensure_conventional_commit(message: str, default_type: str = "feat") -> str:
    """Ensure a commit message follows Conventional Commits, prepending default_type if not.

    Args:
        message: The original commit message.
        default_type: Default conventional commit type (e.g. 'feat', 'fix', 'chore').

    Returns:
        Formatted Conventional Commit message.
    """
    cleaned = message.strip()
    if not cleaned:
        return f"{default_type}: update codebase"

    if is_conventional_commit(cleaned):
        return cleaned

    return f"{default_type}: {cleaned}"


class GitHandler:
    """Asynchronous Git client operating inside a specific repository."""

    def __init__(self, repo_path: Path | str = ".") -> None:
        """Initialize GitHandler for a given repository path.

        Args:
            repo_path: Root path of the git repository.
        """
        self.repo_path = Path(repo_path).resolve()
        self._git_bin = shutil.which("git") or "git"

    async def _run_git(
        self,
        *args: str,
        check: bool = True,
        timeout: float = 60.0,
    ) -> tuple[int, str, str]:
        """Execute a git command asynchronously in the repository directory.

        Args:
            *args: Command line arguments to pass to git.
            check: Whether to raise GitCommandError if the command exits with non-zero code.
            timeout: Maximum allowed execution time in seconds.

        Returns:
            Tuple of (returncode, stdout, stderr).

        Raises:
            GitCommandError: If check=True and process returns a non-zero exit code.
            GitError: On execution failure or timeout.
        """
        cmd_str = f"git {' '.join(args)}"
        try:
            proc = await asyncio.create_subprocess_exec(
                self._git_bin,
                *args,
                cwd=str(self.repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise GitError(f"Git command '{cmd_str}' timed out after {timeout} seconds.") from exc
        except Exception as exc:
            raise GitError(f"Failed to execute git command '{cmd_str}': {exc}") from exc

        returncode = proc.returncode if proc.returncode is not None else 0
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if check and returncode != 0:
            raise GitCommandError(
                cmd=cmd_str,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )

        return (returncode, stdout, stderr)

    async def get_current_branch(self) -> str:
        """Get the name of the currently checked out branch.

        Returns:
            Current branch name.
        """
        # Try git branch --show-current first (git >= 2.22)
        code, stdout, _ = await self._run_git("branch", "--show-current", check=False)
        branch = stdout.strip()
        if branch:
            return branch

        # Fallback to git rev-parse --abbrev-ref HEAD
        code, stdout, _ = await self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        return stdout.strip()

    async def branch_exists(self, branch_name: str) -> bool:
        """Check if a branch exists locally in the repository.

        Args:
            branch_name: Name of branch to check.

        Returns:
            True if branch exists locally, False otherwise.
        """
        code, _, _ = await self._run_git(
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch_name}",
            check=False,
        )
        return code == 0

    async def create_feature_branch(self, slug: str, base_branch: str = "main") -> str:
        """Create and checkout a feature branch named feat/SPEC-<slug>.

        If the branch already exists, it checks out the existing branch.

        Args:
            slug: Task identifier or feature name slug.
            base_branch: Optional base branch to branch off from if not on it.

        Returns:
            The created or checked out branch name.
        """
        raw_slug = slug.strip()

        # Build consistent branch name
        if raw_slug.startswith("feat/"):
            branch_name = f"feat/{sanitize_slug(raw_slug[5:])}"
        elif raw_slug.startswith("spec-"):
            branch_name = f"feat/{sanitize_slug(raw_slug)}"
        elif raw_slug.startswith("SPEC-"):
            branch_name = f"feat/SPEC-{sanitize_slug(raw_slug[5:])}"
        else:
            branch_name = f"feat/SPEC-{sanitize_slug(raw_slug)}"

        exists = await self.branch_exists(branch_name)
        if exists:
            logger.info("Branch '%s' already exists. Checking it out.", branch_name)
            await self._run_git("checkout", branch_name)
        else:
            logger.info("Creating and checking out feature branch '%s'.", branch_name)
            await self._run_git("checkout", "-b", branch_name)

        return branch_name

    async def get_diff(self, target_branch: str = "main") -> str:
        """Get git diff between target_branch and HEAD (origin/<target_branch>...HEAD).

        Falls back to local target_branch...HEAD or HEAD~1 if remote ref is unavailable.

        Args:
            target_branch: Base branch to compare against (default: 'main').

        Returns:
            Unified diff output as a string.
        """
        # 1. Attempt diff against origin/target_branch...HEAD
        code, stdout, _ = await self._run_git(
            "diff",
            f"origin/{target_branch}...HEAD",
            check=False,
        )
        if code == 0:
            return stdout

        # 2. Fallback to local target_branch...HEAD
        code, stdout, _ = await self._run_git(
            "diff",
            f"{target_branch}...HEAD",
            check=False,
        )
        if code == 0:
            return stdout

        # 3. Fallback to diff of uncommitted changes or HEAD
        code, stdout, _ = await self._run_git("diff", "HEAD", check=False)
        if code == 0:
            return stdout

        return ""

    async def has_uncommitted_changes(self) -> bool:
        """Check whether there are unstaged or uncommitted changes.

        Returns:
            True if git status --porcelain reports changes.
        """
        _, stdout, _ = await self._run_git("status", "--porcelain")
        return bool(stdout.strip())

    async def get_recent_commits(self, count: int = 5) -> str:
        """Get summary of recent commits on current branch.

        Args:
            count: Number of recent commits to return.

        Returns:
            Formatted log lines of recent commits.
        """
        code, stdout, _ = await self._run_git(
            "log",
            "-n",
            str(max(1, count)),
            "--pretty=format:%h %s",
            check=False,
        )
        return stdout.strip() if code == 0 else ""

    async def commit_changes(self, message: str) -> str:
        """Stage all changes and commit with Conventional Commits message.

        Args:
            message: Commit message (will be formatted if not already conventional).

        Returns:
            Commit hash or commit summary.
        """
        formatted_message = ensure_conventional_commit(message)

        # Stage all changes
        await self._run_git("add", "-A")

        # Check if there is anything to commit
        has_changes = await self.has_uncommitted_changes()
        if not has_changes:
            logger.info("No changes to commit in working tree.")
            # Return current HEAD commit hash
            _, stdout, _ = await self._run_git("rev-parse", "HEAD", check=False)
            return stdout.strip()

        # Commit changes
        await self._run_git("commit", "-m", formatted_message)

        # Return new HEAD commit hash
        _, stdout, _ = await self._run_git("rev-parse", "HEAD")
        return stdout.strip()

    async def push_branch(
        self,
        branch: str | None = None,
        remote: str = "origin",
        set_upstream: bool = True,
    ) -> str:
        """Push branch to the specified remote repository.

        Args:
            branch: Branch name to push (defaults to current branch).
            remote: Remote repository name (default: 'origin').
            set_upstream: Whether to include -u / --set-upstream flag.

        Returns:
            Output from git push.
        """
        target_branch = branch or await self.get_current_branch()
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args.extend([remote, target_branch])

        _, stdout, stderr = await self._run_git(*args)
        return (stdout + "\n" + stderr).strip()


# Convenience module-level functions
async def create_feature_branch(
    slug: str,
    repo_path: Path | str = ".",
    base_branch: str = "main",
) -> str:
    """Create and checkout a feature branch."""
    handler = GitHandler(repo_path=repo_path)
    return await handler.create_feature_branch(slug=slug, base_branch=base_branch)


async def get_diff(
    target_branch: str = "main",
    repo_path: Path | str = ".",
) -> str:
    """Get git diff between target_branch and HEAD."""
    handler = GitHandler(repo_path=repo_path)
    return await handler.get_diff(target_branch=target_branch)


async def commit_changes(
    message: str,
    repo_path: Path | str = ".",
) -> str:
    """Stage all changes and commit with Conventional Commits message."""
    handler = GitHandler(repo_path=repo_path)
    return await handler.commit_changes(message=message)


async def push_branch(
    branch: str | None = None,
    remote: str = "origin",
    repo_path: Path | str = ".",
) -> str:
    """Push branch to remote."""
    handler = GitHandler(repo_path=repo_path)
    return await handler.push_branch(branch=branch, remote=remote)


async def get_recent_commits(
    count: int = 5,
    repo_path: Path | str = ".",
) -> str:
    """Get recent commit summaries."""
    handler = GitHandler(repo_path=repo_path)
    return await handler.get_recent_commits(count=count)
