"""Command Gatekeeper and Policy Engine for MergeFlow.

Filters, validates, and auto-approves developer agent shell commands:
1. Working directory containment within repository root.
2. Prevention of path traversal escapes (../ resolving outside repo).
3. Immediate rejection against hard blocklist rules.
4. Immediate fast-path approval for safe build/test/inspection tools.
5. Micro-LLM fallback audit for commands in gray zones.
"""

from enum import Enum
import logging
from pathlib import Path
import re
import shlex
from typing import Any

from pydantic import BaseModel, Field

from core.provider import BaseLLMProvider
from security.allow_block_list import (
    check_hard_blocklist,
    check_sensitive_paths,
    is_fast_path_allowed,
)

logger = logging.getLogger(__name__)


class GatekeeperDecision(str, Enum):
    """Decision enum for gatekeeper command evaluations."""

    ALLOW = "ALLOW"
    DENY = "DENY"


class GatekeeperResult(BaseModel):
    """Structured decision output from Gatekeeper command evaluation."""

    is_allowed: bool = Field(
        ...,
        description="Whether the command is permitted to execute.",
    )
    decision: GatekeeperDecision = Field(
        ...,
        description="ALLOW or DENY verdict.",
    )
    reason: str = Field(
        ...,
        description="Explanation or rule justification for the decision.",
    )
    rule_matched: str | None = Field(
        default=None,
        description="Identifier of matched blocklist rule if applicable.",
    )
    audit_used: bool = Field(
        default=False,
        description="Whether the LLM auditor was consulted.",
    )


AUDIT_SYSTEM_PROMPT = """Du bist ein Sicherheits-Auditor für autonome Shell-Befehle in einem Software-Repository.
Analysiere den vorgeschlagenen Befehl im Kontext des Repositories und des Ziels.
Befehle, die neue Abhängigkeiten installieren (z. B. pip install, npm install), Build-Schritte ausführen oder harmlose Skripte aufrufen, dürfen mit ALLOW bestätigt werden, sofern sie keine Malware, Reverse Shells, Datenleaks, zerstörerische Löschungen oder Systemschäden verursachen.
Antworte exakt im Format:
ALLOW: <Begründung>
oder
DENY: <Begründung>"""


class AuditLLM:
    """Micro-LLM Auditor for evaluating shell commands falling into gray zones."""

    def __init__(self, provider: BaseLLMProvider | None = None) -> None:
        self.provider = provider

    async def audit(self, command: str, task_goal: str = "") -> tuple[GatekeeperDecision, str]:
        """Audits a command using the configured LLM provider.

        Prompt format:
        "Analysiere, ob der Befehl `{command}` für das Ziel `{task_goal}` im Repo sicher ist.
        Antworte mit ALLOW oder DENY und Begründung."

        Args:
            command: The command line to audit.
            task_goal: The developer agent's current objective/context.

        Returns:
            Tuple of (GatekeeperDecision, reason_string).
        """
        if self.provider is None:
            return (
                GatekeeperDecision.DENY,
                "Command is not on fast-path allowlist and no LLM provider is configured for audit.",
            )

        goal_text = task_goal.strip() if task_goal else "Allgemeine Entwicklungs- und Testaufgabe"
        prompt = (
            f"Analysiere, ob der Befehl `{command}` für das Ziel `{goal_text}` im Repo sicher ist. "
            "Antworte mit ALLOW oder DENY und Begründung."
        )

        try:
            raw_response = await self.provider.complete(
                prompt=prompt,
                system_prompt=AUDIT_SYSTEM_PROMPT,
            )
            response_text = raw_response if isinstance(raw_response, str) else str(raw_response)
            response_text = response_text.strip()
            return self._parse_audit_response(response_text)
        except Exception as exc:
            logger.error("LLM Auditor call failed: %s", exc)
            return (
                GatekeeperDecision.DENY,
                f"LLM Auditor call failed: {exc}. Defaulting to fail-closed DENY.",
            )

    @staticmethod
    def _parse_audit_response(response: str) -> tuple[GatekeeperDecision, str]:
        """Parses the text output of the LLM audit call."""
        cleaned = response.strip()

        # Check for ALLOW
        match_allow = re.search(r"\bALLOW\b(?:\s*[:\-]?\s*(.*))?", cleaned, re.IGNORECASE | re.DOTALL)
        match_deny = re.search(r"\bDENY\b(?:\s*[:\-]?\s*(.*))?", cleaned, re.IGNORECASE | re.DOTALL)

        if match_allow and not match_deny:
            reason = match_allow.group(1).strip() if match_allow.group(1) else "Approved by LLM Auditor."
            return (GatekeeperDecision.ALLOW, reason)

        if match_deny:
            reason = match_deny.group(1).strip() if match_deny.group(1) else "Rejected by LLM Auditor."
            return (GatekeeperDecision.DENY, reason)

        # Ambiguous output: default to fail-closed
        return (
            GatekeeperDecision.DENY,
            f"Ambiguous LLM audit response: '{cleaned[:100]}'. Defaulting to DENY.",
        )


class Gatekeeper:
    """Validates, filters, and authorizes shell commands for the Dev Agent."""

    def __init__(
        self,
        repo_root: Path | str,
        llm_provider: BaseLLMProvider | AuditLLM | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        if isinstance(llm_provider, AuditLLM):
            self.auditor = llm_provider
        else:
            self.auditor = AuditLLM(llm_provider)

    def _extract_command_tokens(self, command: str) -> list[str]:
        """Tokenizes the command line safely across platforms."""
        try:
            return shlex.split(command, posix=False)
        except ValueError:
            return command.split()

    def check_path_escapes(self, command: str, cwd: Path) -> str | None:
        """Inspects command tokens for directory traversal escaping repo_root.

        Args:
            command: Command line to inspect.
            cwd: Current execution directory (already validated within repo_root).

        Returns:
            Error message if a path escapes repo_root, otherwise None.
        """
        tokens = self._extract_command_tokens(command)

        for token in tokens:
            # Strip quotes if any
            clean_token = token.strip("\"'")

            # Check tilde expansion (~/...)
            if clean_token.startswith("~"):
                expanded = Path(clean_token).expanduser().resolve()
                if not self._is_within_repo(expanded):
                    return f"Path '{clean_token}' expands outside repository root."

            # Check relative directory traversals (..)
            if ".." in clean_token:
                try:
                    resolved = (cwd / clean_token).resolve()
                    if not self._is_within_repo(resolved):
                        return f"Path traversal '{clean_token}' resolves outside repository root."
                except Exception as exc:
                    return f"Invalid path construct '{clean_token}': {exc}"

            # Check absolute paths (e.g. /etc/..., C:\...)
            if Path(clean_token).is_absolute():
                # Allow standard harmless null device
                if clean_token in ("/dev/null", "NUL", "\\dev\\null"):
                    continue
                resolved = Path(clean_token).resolve()
                if not self._is_within_repo(resolved):
                    return f"Absolute path '{clean_token}' points outside repository root."

        return None

    def _is_within_repo(self, path: Path) -> bool:
        """Checks if a resolved path is within repo_root."""
        try:
            return path.resolve().is_relative_to(self.repo_root)
        except (ValueError, AttributeError):
            try:
                path.resolve().relative_to(self.repo_root)
                return True
            except ValueError:
                return False

    async def validate_command(
        self,
        command: str,
        cwd: Path | str | None = None,
        task_goal: str = "",
    ) -> GatekeeperResult:
        """Validates a command against sandbox rules, blocklist, allowlist, and LLM auditor.

        Args:
            command: Shell command to evaluate.
            cwd: Working directory for command execution (defaults to repo_root).
            task_goal: Agent's task context or goal for LLM auditing.

        Returns:
            GatekeeperResult with decision, reason, and metadata.
        """
        cmd_clean = command.strip()
        if not cmd_clean:
            return GatekeeperResult(
                is_allowed=False,
                decision=GatekeeperDecision.DENY,
                reason="Empty command is not allowed.",
            )

        # 1. Sandbox rule: Working directory containment check
        target_cwd = (Path(cwd).resolve()) if cwd else self.repo_root
        if not self._is_within_repo(target_cwd):
            return GatekeeperResult(
                is_allowed=False,
                decision=GatekeeperDecision.DENY,
                reason=f"Working directory '{target_cwd}' is outside repository root '{self.repo_root}'.",
            )

        # 2. Path escapes & traversal checks in command arguments
        escape_reason = self.check_path_escapes(cmd_clean, target_cwd)
        if escape_reason:
            return GatekeeperResult(
                is_allowed=False,
                decision=GatekeeperDecision.DENY,
                reason=escape_reason,
            )

        # 3. Sensitive paths check (.ssh, .aws, /etc/shadow, etc.)
        if check_sensitive_paths(cmd_clean):
            return GatekeeperResult(
                is_allowed=False,
                decision=GatekeeperDecision.DENY,
                reason="Command accesses sensitive credentials or host system paths.",
            )

        # 4. Hard Blocklist check
        matched_block = check_hard_blocklist(cmd_clean)
        if matched_block:
            return GatekeeperResult(
                is_allowed=False,
                decision=GatekeeperDecision.DENY,
                reason=f"Hard blocklist violation: {matched_block.description}",
                rule_matched=matched_block.id,
            )

        # 5. Fast-Path / Allowlist check
        if is_fast_path_allowed(cmd_clean):
            return GatekeeperResult(
                is_allowed=True,
                decision=GatekeeperDecision.ALLOW,
                reason="Fast-path allowlist approved.",
                audit_used=False,
            )

        # 6. LLM-Auditor Fallback for gray zones
        logger.info("Command '%s' routed to LLM Auditor for goal: '%s'", cmd_clean, task_goal)
        decision, reason = await self.auditor.audit(cmd_clean, task_goal)
        return GatekeeperResult(
            is_allowed=decision == GatekeeperDecision.ALLOW,
            decision=decision,
            reason=f"LLM Auditor: {reason}",
            audit_used=True,
        )
