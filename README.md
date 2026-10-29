# MergeFlow: Autonomous Multi-Agent Orchestrator

MergeFlow is a modular, autonomous multi-agent orchestration framework designed for automated software development lifecycle tasks, from requirements discovery and spec generation to coding, CI remediation, and automated peer reviews.

## Architecture Overview

```
MergeFlow/
├── config.yaml                # Example configuration (models, timeouts, retries, CLI binaries)
├── pyproject.toml             # Project metadata, dependencies, and test settings
├── requirements.txt           # Pip dependencies
├── core/
│   ├── __init__.py            # Core package exports
│   ├── config.py              # YAML and Pydantic Settings configuration loader
│   ├── models.py              # Domain & workflow state models (Pydantic v2)
│   └── provider.py            # LLM provider abstraction (CLI & API with JSON schema support)
├── security/
│   ├── __init__.py            # Security package exports
│   ├── allow_block_list.py    # Hard blocklist rules & fast-path allowlist patterns
│   ├── gatekeeper.py          # Command validation, sandbox containment & LLM auditor fallback
│   └── shell_runner.py        # Async subprocess shell runner with streaming and timeouts
└── tests/
    ├── test_config.py         # Config loading and validation test suite
    ├── test_gatekeeper.py     # Gatekeeper policies, sandbox escapes, LLM audit & runner tests
    ├── test_models.py         # Workflow state, findings, and spec test suite
    └── test_provider.py       # CLI and API provider test suite (mocked)
```

---

## Phase 1: Core Foundation & Provider Abstraction

1. **Configuration Management (`core/config.py`)**:
   - Powered by `pydantic-settings` with YAML file loader and environment variable override support (`MERGEFLOW_` prefix).
   - Manages CLI tool paths (e.g. `claude -p`, `antigravity run`), timeouts, API keys, endpoints, and workflow retry thresholds.
   - Can be loaded with `load_config("config.yaml")` or initialized with defaults.

2. **State & Domain Models (`core/models.py`)**:
   - **`WorkflowState`**: Tracks task execution across phases (`SPEC_DISCOVERY`, `CODING`, `CI_LOOP`, `REVIEW`, `DONE`, `FAILED`), branch information, CI retry counts, review retry counts, and findings.
   - **`Finding`**: Structured representation of bugs, security vulnerabilities, or quality recommendations with severity levels and file/line references.
   - **`ReviewResult`**: Feedback from code reviewer agents including approval status and findings.
   - **`Specification`**: Extracted technical requirements with acceptance criteria.

3. **LLM Provider Abstraction (`core/provider.py`)**:
   - **`BaseLLMProvider`**: Common abstract interface with signature:
     ```python
     async def complete(
         self,
         prompt: str,
         system_prompt: str = "",
         json_schema: type[BaseModel] | None = None
     ) -> str | BaseModel
     ```
   - **`CLIProvider`**: Executes local CLI tools asynchronously via `asyncio.create_subprocess_exec`, handling timeouts, output decoding, error diagnostics, and JSON schema extraction.
   - **`APIProvider`**: Direct HTTP calls to OpenAI-compatible endpoints or LiteLLM gateways via `httpx`.
   - **`get_provider`**: Factory function instantiating the right provider based on configuration or explicit provider type (`"cli"`, `"api"`, `"claude"`, `"antigravity"`).

---

## Phase 2: Command-Gatekeeper & Auto-Approver

Phase 2 establishes a secure, autonomous shell execution sandbox for the Dev Agent, validating and executing commands without requiring human interaction while preventing system and directory damage.

1. **Policy Engine (`security/allow_block_list.py` & `security/gatekeeper.py`)**:
   - **Hard Blocklist (RegEx)**: Immediate rejection for destructive commands (`rm -rf /`, `mkfs`, `dd`, `git push --force origin main`, piping web content `curl ... | bash`, `chmod 777 /`, tampering with `.ssh/`, `.env` exfiltration, fork bombs, and system power commands).
   - **Fast-Path / Allowlist**: Direct auto-approval for safe build, test, and inspection tools (`git status`, `git add`, `git commit`, `git diff`, `pytest`, `npm test`, `cargo build`, `cat`, `ls`, `ruff`, `flake8`, `mypy`).
   - **Sandbox Rule**: Working directory is strictly restricted to the repository root (`cwd=repo_root`). Forbids paths outside the repository (`../` cannot resolve above the repository root; absolute paths pointing to external host paths are rejected).
   - **LLM-Auditor Fallback**: Commands in gray zones (e.g. `pip install <package>`, custom scripts, new build targets) trigger a fast Micro-LLM call (`AuditLLM`) to evaluate contextual safety before execution.

2. **Subprocess Shell Runner (`security/shell_runner.py`)**:
   - Method:
     ```python
     async def run_command(command: str, cwd: Path, timeout_seconds: int = 120) -> CommandResult
     ```
   - Wraps `asyncio.create_subprocess_shell` with live stream reading (stdout/stderr) and configurable execution timeouts.
   - Returns a structured `CommandResult`:
     ```python
     exit_code: int
     stdout: str
     stderr: str
     duration: float
     was_blocked: bool
     block_reason: str | None
     ```

---

## Installation & Testing

### Requirements
- Python 3.11 or higher

### Setup Virtual Environment
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Run Tests
```bash
pytest
```
