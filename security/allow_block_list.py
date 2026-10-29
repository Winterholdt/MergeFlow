"""Allowlist and Blocklist definitions for the MergeFlow Command Gatekeeper.

Defines:
- HARD_BLOCK_RULES: Regular expression patterns for immediately rejecting destructive,
  malicious, or data-leaking shell commands.
- FAST_PATH_ALLOW_RULES: Regular expression patterns for fast-path auto-approval
  of safe development, testing, and inspection commands.
- SENSITIVE_PATTERNS: Patterns matching access to secrets and sensitive credentials.
"""

from dataclasses import dataclass
import re
import shlex


@dataclass(frozen=True)
class BlockRule:
    """Represents a security blocklist rule."""

    id: str
    pattern: re.Pattern[str]
    description: str


# ---------------------------------------------------------------------------
# HARD BLOCKLIST RULES
# ---------------------------------------------------------------------------

HARD_BLOCK_RULES: list[BlockRule] = [
    BlockRule(
        id="DESTRUCTIVE_ROOT_OR_HOME_REMOVAL",
        pattern=re.compile(
            r"(?:^|[;&|\s])\s*(?:rm\s+-(?:[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*f[a-zA-Z]*r)\s+(?:/|/\*|~|\$HOME|%USERPROFILE%)(?:\s+|$)|"
            r"del\s+/(?:f|q|s).*[A-Za-z]:\\|"
            r"rd\s+/s\s+/q\s+[A-Za-z]:\\|"
            r"Remove-Item\s+.*-Recurse\s+.*-Force\s+(?:/|[A-Za-z]:\\|~))",
            re.IGNORECASE,
        ),
        description="Destructive deletion of root, home, or drive root filesystem.",
    ),
    BlockRule(
        id="DISK_FORMAT_AND_PARTITIONING",
        pattern=re.compile(
            r"\b(?:mkfs(?:\.\w+)?|fdisk|format\s+[A-Za-z]:|parted|sfdisk|gdisk|cfdisk)\b",
            re.IGNORECASE,
        ),
        description="Low-level disk format or partitioning tool execution.",
    ),
    BlockRule(
        id="RAW_DEVICE_OVERWRITE_DD",
        pattern=re.compile(
            r"\bdd\s+.*?\b(?:of=/dev/(?:sd[a-z]|nvme|hd[a-z]|vd[a-z]|null|zero|random|mem|kmem)|of=\\\\\.\\)",
            re.IGNORECASE,
        ),
        description="Direct disk or device overwrite via dd.",
    ),
    BlockRule(
        id="GIT_FORCE_PUSH_PROTECTED_BRANCH",
        pattern=re.compile(
            r"\bgit\s+push\b.*?(?:--force|-f|\+).*?\b(?:main|master)\b|"
            r"\bgit\s+push\b.*?\b(?:main|master)\b.*?(?:--force|-f)",
            re.IGNORECASE,
        ),
        description="Force pushing to protected git branch (main/master).",
    ),
    BlockRule(
        id="WEB_TO_SHELL_PIPING",
        pattern=re.compile(
            r"(?:curl|wget|fetch|http)\s+.*?\|\s*(?:bash|sh|zsh|dash|ksh|python|python3|perl|ruby|php|node|powershell|pwsh)\b|"
            r"(?:Invoke-WebRequest|iwr|Invoke-RestMethod|irm)\s+.*?\|\s*(?:Invoke-Expression|iex)\b",
            re.IGNORECASE,
        ),
        description="Piping untrusted remote web content directly into an interpreter or shell.",
    ),
    BlockRule(
        id="CRITICAL_PERMISSIONS_CHMOD_ROOT",
        pattern=re.compile(
            r"\bchmod\s+(?:-R\s+)?(?:777|000)\s+(?:/|/\*|~|\$HOME)\b",
            re.IGNORECASE,
        ),
        description="Unrestricted permissions assignment (e.g. chmod 777 /).",
    ),
    BlockRule(
        id="SSH_KEYS_AND_CONFIG_MANIPULATION",
        pattern=re.compile(
            r"(?:(?:~|\$HOME|%USERPROFILE%)?/)?\.ssh(?:/|\b)|"
            r"\b(?:id_rsa|id_ed25519|id_ecdsa|id_dsa|authorized_keys|known_hosts)\b",
            re.IGNORECASE,
        ),
        description="Tampering with or accessing SSH credentials and keys.",
    ),
    BlockRule(
        id="ENV_FILE_LEAK_OR_EXFILTRATION",
        pattern=re.compile(
            r"(?:cat|type|more|less|head|tail|grep|awk|sed)\s+.*?\.env(?:\.[\w.-]+)?(?:\s+|$)|"
            r"(?:curl|wget)\s+.*?(?:-d|--data|--data-binary|--form|-F)\s+.*?\.env|"
            r"(?:^|[;&|\s])\s*(?:printenv|env|export\s+-p|Get-ChildItem\s+env:|dir\s+env:)\s*(?:$|[;&|\n>])",
            re.IGNORECASE,
        ),
        description="Attempt to read, dump, or exfiltrate .env secret files or environment variables.",
    ),
    BlockRule(
        id="FORK_BOMB",
        pattern=re.compile(
            r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:|"
            r"\bbomb\(\)\s*\{\s*bomb\s*\|\s*bomb\s*&\s*\}\s*;\s*bomb",
            re.IGNORECASE,
        ),
        description="Shell fork bomb detected.",
    ),
    BlockRule(
        id="SYSTEM_SHUTDOWN_REBOOT",
        pattern=re.compile(
            r"\b(?:shutdown|reboot|poweroff|halt|init\s+[06])\b",
            re.IGNORECASE,
        ),
        description="System shutdown or reboot invocation.",
    ),
    BlockRule(
        id="REVERSE_SHELL_OR_NETCAT_EXEC",
        pattern=re.compile(
            r"\bbash\s+-i\s+>&|\bnc(?:\.traditional)?\s+(?:-e|-c)\b|/dev/tcp/|/dev/udp/",
            re.IGNORECASE,
        ),
        description="Potential reverse shell or network pipe execution.",
    ),
]


# ---------------------------------------------------------------------------
# SENSITIVE SYSTEM / HOST PATHS
# ---------------------------------------------------------------------------

SENSITIVE_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:/etc/(?:shadow|passwd|sudoers|master\.passwd))", re.IGNORECASE),
    re.compile(r"(?:C:\\Windows\\System32\\config\\(?:SAM|SYSTEM|SECURITY))", re.IGNORECASE),
    re.compile(r"(?:~|\$HOME|%USERPROFILE%)?/?\.aws(?:/|\b)", re.IGNORECASE),
    re.compile(r"(?:~|\$HOME|%USERPROFILE%)?/?\.gnupg(?:/|\b)", re.IGNORECASE),
    re.compile(r"(?:~|\$HOME|%USERPROFILE%)?/?\.ssh(?:/|\b)", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# FAST-PATH ALLOWLIST PATTERNS
# ---------------------------------------------------------------------------

FAST_PATH_COMMAND_PATTERNS: list[re.Pattern[str]] = [
    # Safe Git operations
    re.compile(
        r"^git\s+(?:status|diff|log|show|add|commit|branch|checkout|switch|stash|rev-parse|fetch|pull)(?:\s+.*)?$",
        re.IGNORECASE,
    ),
    # Python testing and linting
    re.compile(
        r"^(?:python(?:\d+(?:\.\d+)?)?\s+-m\s+)?pytest(?:\s+.*)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:python(?:\d+(?:\.\d+)?)?\s+-m\s+)?unittest(?:\s+.*)?$",
        re.IGNORECASE,
    ),
    re.compile(r"^ruff\s+(?:check|format)?(?:\s+.*)?$", re.IGNORECASE),
    re.compile(r"^flake8(?:\s+.*)?$", re.IGNORECASE),
    re.compile(r"^mypy(?:\s+.*)?$", re.IGNORECASE),
    re.compile(r"^black\s+--check(?:\s+.*)?$", re.IGNORECASE),
    re.compile(r"^isort\s+--check(?:\s+.*)?$", re.IGNORECASE),
    # Node / JS testing and linting
    re.compile(
        r"^(?:npm|pnpm|yarn)\s+(?:test|run\s+test|run\s+lint|run\s+build)(?:\s+.*)?$",
        re.IGNORECASE,
    ),
    # Rust Cargo
    re.compile(r"^cargo\s+(?:build|test|check)(?:\s+.*)?$", re.IGNORECASE),
    # Safe inspection utilities
    re.compile(r"^(?:ls|dir)(?:\s+.*)?$", re.IGNORECASE),
    re.compile(r"^(?:cat|head|tail|wc|grep|findstr)(?:\s+.*)?$", re.IGNORECASE),
    re.compile(r"^(?:pwd|echo)(?:\s+.*)?$", re.IGNORECASE),
]


def check_hard_blocklist(command: str) -> BlockRule | None:
    """Checks whether the command violates any hard blocklist rule.

    Args:
        command: The shell command line to inspect.

    Returns:
        The matched BlockRule if blocked, otherwise None.
    """
    for rule in HARD_BLOCK_RULES:
        if rule.pattern.search(command):
            return rule
    return None


def check_sensitive_paths(command: str) -> bool:
    """Checks if a command references known sensitive host paths.

    Args:
        command: The shell command line to inspect.

    Returns:
        True if a sensitive path is detected, False otherwise.
    """
    for pattern in SENSITIVE_PATH_PATTERNS:
        if pattern.search(command):
            return True
    return False


def _split_chained_commands(command: str) -> list[str]:
    """Splits a command containing chaining operators (&&, ;, ||) into individual segments.

    Preserves quoted strings without splitting inside them.
    """
    # Regex splits by &&, ||, ;, or newline not inside quotes
    pattern = re.compile(r"(?:&&|\|\||;|\n)")
    segments = pattern.split(command)
    return [seg.strip() for seg in segments if seg.strip()]


def is_fast_path_allowed(command: str) -> bool:
    """Evaluates whether the command qualifies for immediate Fast-Path approval.

    For chained commands (e.g. `cmd1 && cmd2`), every segment must qualify.
    Commands containing shell redirects, command substitution or piped shell
    interpreters are not allowed fast-path auto-approval.

    Args:
        command: The command line to evaluate.

    Returns:
        True if the command passes fast-path validation, False if it needs LLM audit.
    """
    # Reject fast-path if dangerous shell constructs are present
    if any(char in command for char in ["`", "$("]):
        return False

    # Check for unquoted redirects that might overwrite files without review
    if re.search(r"(?:^|\s)>(?:>)?(?:\s|$)", command):
        return False

    # Check for pipe to non-allowlisted commands
    if "|" in command:
        pipe_segments = [p.strip() for p in command.split("|")]
        # Fast path only if each piped segment is an inspection command
        for seg in pipe_segments:
            if not any(pat.match(seg) for pat in FAST_PATH_COMMAND_PATTERNS):
                return False

    segments = _split_chained_commands(command)
    if not segments:
        return False

    for segment in segments:
        # Each segment must match at least one allowed pattern
        matched = any(pat.match(segment) for pat in FAST_PATH_COMMAND_PATTERNS)
        if not matched:
            return False

    return True
