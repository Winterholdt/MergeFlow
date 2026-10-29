"""Remote VCS hosting wrapper (GitHub / GitLab) for MergeFlow.

Interacts with GitHub CLI (`gh`) or GitLab CLI (`glab`) to manage pull/merge requests
and inspect CI pipeline / checks statuses.
"""

import asyncio
from collections.abc import Callable
from enum import Enum
import logging
from pathlib import Path
import re
import shutil
from typing import Any

from core.models import PipelineCheck, PipelineState, PipelineStatus

logger = logging.getLogger(__name__)


class VCSType(str, Enum):
    """Supported remote version control hosting platforms."""

    GITHUB = "github"
    GITLAB = "gitlab"
    AUTO = "auto"


class RemoteVCSError(Exception):
    """Raised when remote VCS CLI operations fail or are not available."""


class RemoteVCS:
    """Interface for remote VCS CLI operations supporting GitHub (gh) and GitLab (glab)."""

    def __init__(
        self,
        repo_path: Path | str = ".",
        vcs_type: VCSType | str = VCSType.AUTO,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        """Initialize RemoteVCS client.

        Args:
            repo_path: Root path of the repository.
            vcs_type: Remote VCS platform (github, gitlab, or auto).
            runner: Optional custom async command runner for testing and dependency injection.
        """
        self.repo_path = Path(repo_path).resolve()
        self._specified_vcs = VCSType(vcs_type) if isinstance(vcs_type, str) else vcs_type
        self._runner = runner

    def detect_vcs_type(self) -> VCSType:
        """Detect whether GitHub (gh) or GitLab (glab) CLI is available.

        Returns:
            Detected VCSType (GITHUB or GITLAB).

        Raises:
            RemoteVCSError: If neither CLI tool is detected.
        """
        if self._specified_vcs in (VCSType.GITHUB, VCSType.GITLAB):
            return self._specified_vcs

        has_gh = bool(shutil.which("gh"))
        has_glab = bool(shutil.which("glab"))

        if has_gh:
            return VCSType.GITHUB
        elif has_glab:
            return VCSType.GITLAB

        raise RemoteVCSError(
            "Neither 'gh' (GitHub CLI) nor 'glab' (GitLab CLI) is installed or available in PATH."
        )

    async def _execute_cli(self, binary: str, *args: str) -> tuple[int, str, str]:
        """Execute a CLI tool command asynchronously.

        Args:
            binary: CLI binary name (e.g. 'gh' or 'glab').
            *args: Arguments for the command.

        Returns:
            Tuple of (exit_code, stdout, stderr).
        """
        if self._runner is not None:
            res = self._runner(binary, *args)
            if asyncio.iscoroutine(res):
                return await res
            return res

        bin_path = shutil.which(binary)
        if not bin_path:
            raise RemoteVCSError(f"CLI binary '{binary}' is not installed or available in PATH.")

        try:
            proc = await asyncio.create_subprocess_exec(
                bin_path,
                *args,
                cwd=str(self.repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
            return (
                proc.returncode if proc.returncode is not None else 0,
                stdout_bytes.decode("utf-8", errors="replace"),
                stderr_bytes.decode("utf-8", errors="replace"),
            )
        except Exception as exc:
            raise RemoteVCSError(f"Failed to execute '{binary} {' '.join(args)}': {exc}") from exc

    async def create_draft_pr(
        self,
        title: str,
        body: str,
        branch: str,
    ) -> str:
        """Create a draft Pull Request (GitHub) or Merge Request (GitLab).

        Args:
            title: Title for the PR/MR.
            body: Description / body markdown for the PR/MR.
            branch: Head / source branch containing the feature changes.

        Returns:
            URL string of the created draft PR / MR.

        Raises:
            RemoteVCSError: If the CLI tool fails or returns non-zero.
        """
        vcs = self.detect_vcs_type()

        if vcs == VCSType.GITHUB:
            # gh pr create --draft --title <title> --body <body> --head <branch>
            args = [
                "pr",
                "create",
                "--draft",
                "--title",
                title,
                "--body",
                body,
                "--head",
                branch,
            ]
            code, stdout, stderr = await self._execute_cli("gh", *args)
            if code != 0:
                raise RemoteVCSError(f"GitHub PR creation failed (code {code}): {stderr.strip() or stdout.strip()}")

            url_match = re.search(r"https?://[^\s]+", stdout)
            if url_match:
                return url_match.group(0).strip()
            return stdout.strip()

        else:
            # glab mr create -d --title <title> --description <body> --source-branch <branch>
            args = [
                "mr",
                "create",
                "-d",
                "--title",
                title,
                "--description",
                body,
                "--source-branch",
                branch,
            ]
            code, stdout, stderr = await self._execute_cli("glab", *args)
            if code != 0:
                raise RemoteVCSError(f"GitLab MR creation failed (code {code}): {stderr.strip() or stdout.strip()}")

            url_match = re.search(r"https?://[^\s]+", stdout)
            if url_match:
                return url_match.group(0).strip()
            return stdout.strip()

    async def get_pipeline_status(self, pr_number: int | None = None) -> PipelineStatus:
        """Query CI pipeline and check statuses for a PR / MR.

        Args:
            pr_number: Optional PR/MR number to inspect.

        Returns:
            PipelineStatus model containing aggregated state, individual checks, and raw output.

        Raises:
            RemoteVCSError: If the CLI tool command fails.
        """
        vcs = self.detect_vcs_type()

        if vcs == VCSType.GITHUB:
            args = ["pr", "checks"]
            if pr_number is not None:
                args.append(str(pr_number))

            code, stdout, stderr = await self._execute_cli("gh", *args)
            if code != 0:
                # If no checks are reported or command failed
                return PipelineStatus(
                    state=PipelineState.UNKNOWN,
                    all_passed=False,
                    checks=[],
                    raw_output=stderr.strip() or stdout.strip(),
                )

            return self._parse_gh_checks(stdout)

        else:
            # glab mr pipeline <pr_number>
            args = ["mr", "pipeline"]
            if pr_number is not None:
                args.append(str(pr_number))

            code, stdout, stderr = await self._execute_cli("glab", *args)
            if code != 0:
                return PipelineStatus(
                    state=PipelineState.UNKNOWN,
                    all_passed=False,
                    checks=[],
                    raw_output=stderr.strip() or stdout.strip(),
                )

            return self._parse_glab_pipeline(stdout)

    @staticmethod
    def _parse_gh_checks(output: str) -> PipelineStatus:
        """Parse text output of `gh pr checks` into structured PipelineStatus.

        Example gh output lines:
        ✓  build   pass   1m 20s   https://github.com/...
        X  test    fail   45s      https://github.com/...
        *  lint    pending -       https://github.com/...
        """
        checks: list[PipelineCheck] = []
        has_failure = False
        has_pending = False
        lines = [line.strip() for line in output.strip().splitlines() if line.strip()]

        for line in lines:
            # Strip leading status symbol if present
            match_symbol = re.match(r"^([✓✔*•~X✗✖!\-\s]+)(.*)$", line)
            symbol_str = ""
            rest = line
            if match_symbol and match_symbol.group(1).strip():
                symbol_str = match_symbol.group(1).strip()
                rest = match_symbol.group(2).strip()

            # Find URL at the end if present
            url = None
            url_match = re.search(r"https?://[^\s]+", rest)
            if url_match:
                url = url_match.group(0)
                rest = rest[:url_match.start()].strip()

            columns = [c.strip() for c in re.split(r"\s{2,}|\t+", rest) if c.strip()]
            if not columns:
                continue

            name = columns[0]
            outcome = columns[1].lower() if len(columns) > 1 else ""

            if symbol_str in ("✓", "✔") or "pass" in outcome or "success" in outcome:
                conclusion = "success"
                status = "completed"
            elif symbol_str in ("X", "✗", "✖", "!") or any(kw in outcome for kw in ("fail", "error", "cancelled")):
                conclusion = "failure"
                status = "completed"
                has_failure = True
            elif symbol_str in ("*", "•", "~") or any(kw in outcome for kw in ("pending", "progress", "queued", "running")):
                conclusion = None
                status = "in_progress"
                has_pending = True
            else:
                conclusion = outcome or "neutral"
                status = "completed"

            checks.append(
                PipelineCheck(
                    name=name,
                    status=status,
                    conclusion=conclusion,
                    details_url=url,
                )
            )

        if not checks:
            # Check if text says "All checks have passed" or similar
            if "pass" in output.lower() or "success" in output.lower():
                state = PipelineState.SUCCESS
                all_passed = True
            elif "fail" in output.lower() or "error" in output.lower():
                state = PipelineState.FAILED
                all_passed = False
            else:
                state = PipelineState.UNKNOWN
                all_passed = False
        elif has_failure:
            state = PipelineState.FAILED
            all_passed = False
        elif has_pending:
            state = PipelineState.RUNNING
            all_passed = False
        else:
            state = PipelineState.SUCCESS
            all_passed = True

        return PipelineStatus(
            state=state,
            all_passed=all_passed,
            checks=checks,
            raw_output=output,
        )

    @staticmethod
    def _parse_glab_pipeline(output: str) -> PipelineStatus:
        """Parse text output of `glab mr pipeline` into structured PipelineStatus."""
        lower = output.lower()
        if any(kw in lower for kw in ("failed", "failure", "error")):
            state = PipelineState.FAILED
            all_passed = False
        elif any(kw in lower for kw in ("running", "pending", "created", "waiting")):
            state = PipelineState.RUNNING
            all_passed = False
        elif any(kw in lower for kw in ("passed", "success", "completed")):
            state = PipelineState.SUCCESS
            all_passed = True
        else:
            state = PipelineState.UNKNOWN
            all_passed = False

        return PipelineStatus(
            state=state,
            all_passed=all_passed,
            checks=[],
            raw_output=output,
        )

    async def merge_pr(
        self,
        pr_number: int | str | None = None,
        auto: bool = True,
        squash: bool = True,
    ) -> tuple[int, str, str]:
        """Merge a Pull Request (GitHub) or Merge Request (GitLab).

        Args:
            pr_number: Optional PR/MR number to merge. If None, merges current branch's PR/MR.
            auto: Whether to enable auto-merge (--auto).
            squash: Whether to squash commits on merge (--squash).

        Returns:
            Tuple of (exit_code, stdout, stderr).

        Raises:
            RemoteVCSError: If the CLI tool fails or returns non-zero exit code.
        """
        vcs = self.detect_vcs_type()

        if vcs == VCSType.GITHUB:
            # gh pr merge [pr_number] [--auto] [--squash]
            args = ["pr", "merge"]
            if pr_number is not None:
                args.append(str(pr_number))
            if auto:
                args.append("--auto")
            if squash:
                args.append("--squash")

            code, stdout, stderr = await self._execute_cli("gh", *args)
            if code != 0:
                raise RemoteVCSError(f"GitHub PR merge failed (code {code}): {stderr.strip() or stdout.strip()}")
            return (code, stdout, stderr)

        else:
            # glab mr merge [pr_number] [--auto] [--squash]
            args = ["mr", "merge"]
            if pr_number is not None:
                args.append(str(pr_number))
            if auto:
                args.append("--auto")
            if squash:
                args.append("--squash")

            code, stdout, stderr = await self._execute_cli("glab", *args)
            if code != 0:
                raise RemoteVCSError(f"GitLab MR merge failed (code {code}): {stderr.strip() or stdout.strip()}")
            return (code, stdout, stderr)


# Convenience module-level functions
async def create_draft_pr(
    title: str,
    body: str,
    branch: str,
    repo_path: Path | str = ".",
    vcs_type: str = "auto",
) -> str:
    """Create a draft Pull/Merge Request."""
    client = RemoteVCS(repo_path=repo_path, vcs_type=vcs_type)
    return await client.create_draft_pr(title=title, body=body, branch=branch)


async def get_pipeline_status(
    pr_number: int | None = None,
    repo_path: Path | str = ".",
    vcs_type: str = "auto",
) -> PipelineStatus:
    """Get CI pipeline status for a PR or MR."""
    client = RemoteVCS(repo_path=repo_path, vcs_type=vcs_type)
    return await client.get_pipeline_status(pr_number=pr_number)


async def merge_pr(
    pr_number: int | str | None = None,
    auto: bool = True,
    squash: bool = True,
    repo_path: Path | str = ".",
    vcs_type: str = "auto",
) -> tuple[int, str, str]:
    """Merge a Pull or Merge Request."""
    client = RemoteVCS(repo_path=repo_path, vcs_type=vcs_type)
    return await client.merge_pr(pr_number=pr_number, auto=auto, squash=squash)
