"""Secure Subprocess Shell Runner for MergeFlow Dev Agent.

Executes authorized shell commands inside the validated repository sandbox,
supporting timeouts, real-time output streaming, and structured CommandResult objects.
"""

import asyncio
from collections.abc import Callable
import logging
from pathlib import Path
import time
from typing import Any

from pydantic import BaseModel, Field

from security.gatekeeper import Gatekeeper

logger = logging.getLogger(__name__)


class CommandResult(BaseModel):
    """Structured result returned by the shell command execution."""

    exit_code: int = Field(
        ...,
        description="Exit status code of the command (-1 for blocked or killed processes).",
    )
    stdout: str = Field(
        default="",
        description="Standard output produced during command execution.",
    )
    stderr: str = Field(
        default="",
        description="Standard error produced during command execution.",
    )
    duration: float = Field(
        default=0.0,
        description="Total duration in seconds.",
    )
    was_blocked: bool = Field(
        default=False,
        description="Indicates whether the command was blocked by Gatekeeper policy.",
    )
    block_reason: str | None = Field(
        default=None,
        description="The detailed justification if the command was blocked.",
    )


class ShellRunner:
    """Manages asynchronous command validation, execution, streaming, and timeouts."""

    def __init__(
        self,
        gatekeeper: Gatekeeper | None = None,
        repo_root: Path | str | None = None,
        output_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        """Initializes the ShellRunner.

        Args:
            gatekeeper: Optional Gatekeeper instance for policy validation.
            repo_root: Optional repository root path if gatekeeper needs auto-initialization.
            output_callback: Optional callable(stream_name: str, line: str) for live streaming.
        """
        if gatekeeper is not None:
            self.gatekeeper = gatekeeper
        else:
            root = Path(repo_root or Path.cwd()).resolve()
            self.gatekeeper = Gatekeeper(repo_root=root)

        self.output_callback = output_callback

    async def _stream_reader(
        self,
        stream: asyncio.StreamReader,
        buffer: list[str],
        stream_name: str,
    ) -> None:
        """Reads stream lines asynchronously, buffers them and triggers output callbacks."""
        while True:
            line_bytes = await stream.readline()
            if not line_bytes:
                break
            line_str = line_bytes.decode("utf-8", errors="replace")
            buffer.append(line_str)
            if self.output_callback:
                try:
                    self.output_callback(stream_name, line_str)
                except Exception as exc:
                    logger.debug("Output callback error: %s", exc)

    async def run_command(
        self,
        command: str,
        cwd: Path,
        timeout_seconds: int = 120,
        task_goal: str = "",
    ) -> CommandResult:
        """Validates and executes a shell command within the repository sandbox.

        Args:
            command: Shell command line to run.
            cwd: Working directory (must reside within repository root).
            timeout_seconds: Maximum allowed execution duration before SIGKILL.
            task_goal: Optional task description context for LLM auditing.

        Returns:
            CommandResult with exit code, outputs, timing, and gatekeeper verdict.
        """
        cwd_path = Path(cwd).resolve()

        # Step 1: Policy validation via Gatekeeper
        gate_decision = await self.gatekeeper.validate_command(
            command=command,
            cwd=cwd_path,
            task_goal=task_goal,
        )

        if not gate_decision.is_allowed:
            logger.warning("Command execution blocked: %s (Reason: %s)", command, gate_decision.reason)
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command blocked by Gatekeeper: {gate_decision.reason}",
                duration=0.0,
                was_blocked=True,
                block_reason=gate_decision.reason,
            )

        # Step 2: Asynchronous subprocess execution
        start_time = time.perf_counter()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            assert proc.stdout is not None
            assert proc.stderr is not None

            # Stream stdout and stderr concurrently with timeout
            stdout_task = asyncio.create_task(
                self._stream_reader(proc.stdout, stdout_lines, "stdout")
            )
            stderr_task = asyncio.create_task(
                self._stream_reader(proc.stderr, stderr_lines, "stderr")
            )

            try:
                await asyncio.wait_for(
                    asyncio.gather(proc.wait(), stdout_task, stderr_task),
                    timeout=float(timeout_seconds),
                )
                exit_code = proc.returncode if proc.returncode is not None else 0
            except asyncio.TimeoutError:
                logger.error("Command '%s' timed out after %ds. Terminating process...", command, timeout_seconds)
                try:
                    proc.terminate()
                    await asyncio.sleep(0.2)
                    if proc.returncode is None:
                        proc.kill()
                    await proc.wait()
                except (ProcessLookupError, OSError):
                    pass

                # Cancel pending stream tasks
                stdout_task.cancel()
                stderr_task.cancel()

                duration = time.perf_counter() - start_time
                stderr_lines.append(f"\n[ERROR] Command timed out after {timeout_seconds} seconds.")
                return CommandResult(
                    exit_code=124,
                    stdout="".join(stdout_lines),
                    stderr="".join(stderr_lines),
                    duration=duration,
                    was_blocked=False,
                    block_reason=None,
                )

        except Exception as exc:
            duration = time.perf_counter() - start_time
            logger.error("Subprocess launch failed: %s", exc)
            return CommandResult(
                exit_code=-1,
                stdout="".join(stdout_lines),
                stderr=f"Subprocess execution error: {exc}",
                duration=duration,
                was_blocked=False,
                block_reason=None,
            )

        duration = time.perf_counter() - start_time
        return CommandResult(
            exit_code=exit_code,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
            duration=duration,
            was_blocked=False,
            block_reason=None,
        )


async def run_command(
    command: str,
    cwd: Path,
    timeout_seconds: int = 120,
    gatekeeper: Gatekeeper | None = None,
    task_goal: str = "",
) -> CommandResult:
    """Convenience function satisfying the required run_command interface.

    Args:
        command: Shell command line to run.
        cwd: Working directory (must be within repo root).
        timeout_seconds: Execution timeout limit.
        gatekeeper: Optional pre-configured Gatekeeper.
        task_goal: Optional task context for LLM auditing.

    Returns:
        Structured CommandResult.
    """
    runner = ShellRunner(gatekeeper=gatekeeper, repo_root=cwd)
    return await runner.run_command(
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        task_goal=task_goal,
    )
