"""Local Git operations engine for MergeFlow orchestrator.

Provides asynchronous Git operations using native asyncio subprocesses:
- Feature branch creation and switching (feat/SPEC-<slug>)
- Diff computation against base branch
- Conventional Commits staging and committing
- Push operations with upstream tracking
"""

import asyncio
from collections.abc import Callable
import logging
from pathlib import Path
import re
import shutil
from typing import Any

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


DEFAULT_MAX_DIFF_LINES = 4000
DEFAULT_MAX_DIFF_WORDS = 20000

TRUNCATION_NOTE = (
    "> [!WARNING]\n"
    "> HINWEIS: Der Diff hat das Token-Limit überschritten (über 4.000 Zeilen oder 20.000 Wörter) "
    "und wurde dateibasiert gekürzt. Enthalten ist die Übersicht (`git diff --stat`) sowie die Diffs der geänderten Kern-Dateien."
)


def split_diff_by_files(diff_text: str) -> list[tuple[str, str]]:
    """Splits unified diff output into a list of (filename, file_diff) pairs."""
    chunks: list[tuple[str, str]] = []
    current_file = ""
    current_lines: list[str] = []

    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current_file:
                chunks.append((current_file, "".join(current_lines)))
            match = re.search(r"diff --git a/(.*?) b/", line)
            current_file = match.group(1) if match else line.strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_file:
        chunks.append((current_file, "".join(current_lines)))

    return chunks


def is_core_file(filename: str) -> bool:
    """Determine whether a file is a core source file versus lockfile/generated asset."""
    lower = filename.lower()
    low_priority_patterns = [
        "lock", "-lock.json", ".lock", "package-lock", "yarn.lock", "pnpm-lock",
        ".min.", ".map", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".pdf",
        "dist/", "build/", ".gen.", "vendor/"
    ]
    return not any(pat in lower for pat in low_priority_patterns)


def truncate_or_split_diff(
    diff: str,
    stat: str = "",
    max_lines: int = DEFAULT_MAX_DIFF_LINES,
    max_words: int = DEFAULT_MAX_DIFF_WORDS,
) -> tuple[str, bool]:
    """Truncates or splits diff by file if line/word limits are exceeded."""
    lines = diff.splitlines()
    words = diff.split()
    if len(lines) <= max_lines and len(words) <= max_words:
        return diff, False

    chunks = split_diff_by_files(diff)
    if not chunks:
        truncated = "\n".join(lines[:max_lines])
        stat_block = f"### Git Diff --stat\n```\n{stat.strip()}\n```\n\n" if stat else ""
        return f"{TRUNCATION_NOTE}\n\n{stat_block}### Gekürzter Diff:\n{truncated}", True

    core_chunks = [c for c in chunks if is_core_file(c[0])]
    other_chunks = [c for c in chunks if not is_core_file(c[0])]
    ordered_chunks = core_chunks + other_chunks

    accumulated: list[str] = []
    cur_lines = 0
    cur_words = 0

    budget_lines = max_lines - 50
    budget_words = max_words - 200

    for fname, chunk in ordered_chunks:
        c_lines = chunk.count("\n") + 1
        c_words = len(chunk.split())
        if cur_lines + c_lines > budget_lines or cur_words + c_words > budget_words:
            continue
        accumulated.append(chunk)
        cur_lines += c_lines
        cur_words += c_words

    body = "".join(accumulated) if accumulated else "".join(c[1] for c in chunks[:1])
    stat_block = f"### Git Diff --stat\n```\n{stat.strip()}\n```\n\n" if stat else ""
    final_diff = f"{TRUNCATION_NOTE}\n\n{stat_block}### Diffs der geänderten Kern-Dateien:\n{body}"
    return final_diff, True


class GitHandler:
    """Asynchronous Git client operating inside a specific repository."""

    def __init__(
        self,
        repo_path: Path | str = ".",
        on_commit_callback: Callable[[str], Any] | None = None,
    ) -> None:
        """Initialize GitHandler for a given repository path.

        Args:
            repo_path: Root path of the git repository.
            on_commit_callback: Optional callback invoked whenever changes are committed.
        """
        self.repo_path = Path(repo_path).resolve()
        self._git_bin = shutil.which("git") or "git"
        self.on_commit_callback = on_commit_callback

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

    async def get_diff_stat(self, target_branch: str = "main") -> str:
        """Get git diff --stat against target branch."""
        code, stdout, _ = await self._run_git(
            "diff",
            "--stat",
            f"origin/{target_branch}...HEAD",
            check=False,
        )
        if code == 0 and stdout.strip():
            return stdout.strip()

        code, stdout, _ = await self._run_git(
            "diff",
            "--stat",
            f"{target_branch}...HEAD",
            check=False,
        )
        if code == 0 and stdout.strip():
            return stdout.strip()

        code, stdout, _ = await self._run_git("diff", "--stat", "HEAD", check=False)
        return stdout.strip() if code == 0 else ""

    async def get_diff(
        self,
        target_branch: str = "main",
        max_lines: int | None = None,
        max_words: int | None = None,
        protect_tokens: bool = False,
    ) -> str:
        """Get git diff between target_branch and HEAD (origin/<target_branch>...HEAD).

        Falls back to local target_branch...HEAD or HEAD~1 if remote ref is unavailable.
        Optionally applies token protection by splitting or truncating if limits are exceeded.

        Args:
            target_branch: Base branch to compare against (default: 'main').
            max_lines: Max diff lines before truncation/splitting.
            max_words: Max diff words before truncation/splitting.
            protect_tokens: If True, enforces DEFAULT_MAX_DIFF_LINES / DEFAULT_MAX_DIFF_WORDS.

        Returns:
            Unified diff output as a string.
        """
        # 1. Attempt diff against origin/target_branch...HEAD
        code, stdout, _ = await self._run_git(
            "diff",
            f"origin/{target_branch}...HEAD",
            check=False,
        )
        raw_diff = ""
        if code == 0:
            raw_diff = stdout
        else:
            # 2. Fallback to local target_branch...HEAD
            code, stdout, _ = await self._run_git(
                "diff",
                f"{target_branch}...HEAD",
                check=False,
            )
            if code == 0:
                raw_diff = stdout
            else:
                # 3. Fallback to diff of uncommitted changes or HEAD
                code, stdout, _ = await self._run_git("diff", "HEAD", check=False)
                if code == 0:
                    raw_diff = stdout

        if not raw_diff:
            return ""

        effective_max_lines = max_lines if max_lines is not None else (DEFAULT_MAX_DIFF_LINES if protect_tokens else None)
        effective_max_words = max_words if max_words is not None else (DEFAULT_MAX_DIFF_WORDS if protect_tokens else None)

        if effective_max_lines is not None or effective_max_words is not None:
            stat = await self.get_diff_stat(target_branch=target_branch)
            lim_lines = effective_max_lines or DEFAULT_MAX_DIFF_LINES
            lim_words = effective_max_words or DEFAULT_MAX_DIFF_WORDS
            truncated_diff, _ = truncate_or_split_diff(
                raw_diff,
                stat=stat,
                max_lines=lim_lines,
                max_words=lim_words,
            )
            return truncated_diff

        return raw_diff

    async def get_review_diff(
        self,
        target_branch: str = "main",
        max_lines: int = DEFAULT_MAX_DIFF_LINES,
        max_words: int = DEFAULT_MAX_DIFF_WORDS,
    ) -> tuple[str, bool]:
        """Fetch git diff specifically formatted and guarded for code reviewers.

        Reviewers standardly receive origin/<target_branch>...HEAD with token protection.

        Args:
            target_branch: Base branch (default: 'main').
            max_lines: Max diff lines before splitting.
            max_words: Max diff words before splitting.

        Returns:
            Tuple of (diff_content: str, was_truncated: bool).
        """
        code, stdout, _ = await self._run_git(
            "diff",
            f"origin/{target_branch}...HEAD",
            check=False,
        )
        raw_diff = ""
        if code == 0:
            raw_diff = stdout
        else:
            code, stdout, _ = await self._run_git("diff", f"{target_branch}...HEAD", check=False)
            raw_diff = stdout if code == 0 else ""

        if not raw_diff:
            code, stdout, _ = await self._run_git("diff", "HEAD", check=False)
            raw_diff = stdout if code == 0 else ""

        if not raw_diff:
            return "", False

        stat = await self.get_diff_stat(target_branch=target_branch)
        return truncate_or_split_diff(
            raw_diff,
            stat=stat,
            max_lines=max_lines,
            max_words=max_words,
        )

    async def check_scope_drift(
        self,
        target_branch: str = "main",
        max_allowed_deletions: int = 20,
        max_total_changes: int = 500,
    ) -> tuple[bool, str]:
        """Inspect git diff --stat against target branch for signs of scope drift.

        Checks whether files were deleted or disproportionately many lines were altered.

        Returns:
            Tuple of (suspected_scope_drift: bool, reason_or_summary: str).
        """
        stat = await self.get_diff_stat(target_branch=target_branch)

        # Check git status for deleted files
        code, status_out, _ = await self._run_git("status", "--porcelain", check=False)
        deleted_files: list[str] = []
        for line in status_out.splitlines():
            line_str = line.strip()
            if line_str.startswith("D ") or line_str.startswith(" D "):
                deleted_files.append(line_str.split()[-1])

        code_d, diff_d, _ = await self._run_git(
            "diff",
            "--name-only",
            "--diff-filter=D",
            f"origin/{target_branch}...HEAD",
            check=False,
        )
        if code_d == 0 and diff_d.strip():
            for df in diff_d.splitlines():
                if df.strip() and df.strip() not in deleted_files:
                    deleted_files.append(df.strip())

        stat_summary_match = re.search(
            r"(\d+)\s+files? changed(?:,\s+(\d+)\s+insertions?\(\+\))?(?:,\s+(\d+)\s+deletions?\(-\))?",
            stat,
        )
        total_insertions = 0
        total_deletions = 0
        files_changed = 0
        if stat_summary_match:
            files_changed = int(stat_summary_match.group(1))
            total_insertions = int(stat_summary_match.group(2) or 0)
            total_deletions = int(stat_summary_match.group(3) or 0)

        drift_reasons: list[str] = []
        if deleted_files:
            drift_reasons.append(f"Dateien gelöscht ({len(deleted_files)}): {', '.join(deleted_files[:3])}")
        elif total_deletions > max_allowed_deletions:
            drift_reasons.append(f"Hohe Anzahl an Löschungen: {total_deletions} Zeilen gelöscht")

        total_lines = total_insertions + total_deletions
        if total_lines > max_total_changes:
            drift_reasons.append(
                f"Unverhältnismäßig viele Zeilen verändert ({total_lines} Zeilen in {files_changed} Dateien)"
            )

        if drift_reasons:
            reason_msg = "; ".join(drift_reasons)
            return True, reason_msg
        return False, ""

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
        commit_hash = stdout.strip()

        if self.on_commit_callback:
            try:
                res = self.on_commit_callback(commit_hash)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.warning("Error in on_commit callback: %s", e)

        return commit_hash

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
