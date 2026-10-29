# MergeFlow: Autonomous Multi-Agent Orchestrator

MergeFlow is a modular, autonomous multi-agent orchestration framework designed for automated software development lifecycle tasks, from requirements discovery and spec generation to coding, CI remediation, and automated peer reviews.

## Architecture Overview

```
MergeFlow/
├── config.yaml                # Example configuration (models, timeouts, retries, CLI binaries)
├── main.py                    # CLI entry-point (Phases 1-7)
├── pyproject.toml             # Project metadata, dependencies, and test settings
├── requirements.txt           # Pip dependencies
├── prompts/
│   ├── spec_discovery.md      # System prompt for Requirements Discovery & Architect Agent
│   ├── developer.md           # Developer Agent system prompt
│   ├── ci_healing.md          # CI Healing Agent system prompt
│   └── review_*.md            # Reviewer persona system prompts (functional, architecture, security, qa_tests)
├── core/
│   ├── __init__.py            # Core package exports
│   ├── config.py              # YAML and Pydantic Settings configuration loader
│   ├── consensus.py           # Phase 7: Consensus evaluation, feedback loop & auto-merge
│   ├── dev_loop.py            # Phase 4: Developer Agent execution loop
│   ├── discovery.py           # Phase 3: Interactive discovery loop, blind spot analysis & spec generation
│   ├── healing_loop.py        # Phase 5: Self-healing CI loop
│   ├── models.py              # Domain & workflow state models (Pydantic v2)
│   ├── orchestrator.py        # Central state-machine connecting Phases 1 through 7
│   ├── provider.py            # LLM provider abstraction (CLI & API with JSON schema support)
│   └── reviewer.py            # Phase 6: Multi-persona code review orchestrator
├── security/
│   ├── __init__.py            # Security package exports
│   ├── allow_block_list.py    # Hard blocklist rules & fast-path allowlist patterns
│   ├── gatekeeper.py          # Command validation, sandbox containment & LLM auditor fallback
│   └── shell_runner.py        # Async subprocess shell runner with streaming and timeouts
├── vcs/
│   ├── ci_watcher.py          # Remote CI pipeline watcher and log extractor
│   ├── git_handler.py         # Git branching, staging, committing & diffing
│   └── remote_vcs.py          # GitHub CLI (gh) / GitLab CLI (glab) interface
└── tests/                     # Unit and integration test suites
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

## Phase 3: Requirements Discovery & Spec Generation

Phase 3 implements the interactive CLI interface and Spec Agent for transforming raw, underspecified tasks into a validated, actionable specification (`SPEC.md`).

1. **Context & Blind-Spot Discovery (`core/discovery.py`)**:
   - Signature:
     ```python
     async def run_discovery_phase(
         raw_requirements: str,
         provider: BaseLLMProvider,
         repo_path: Path,
         console: Console | None = None,
         input_func: Callable[[str], str] | None = None,
     ) -> str
     ```
   - Automatically gathers repository context (`README.md`, `pyproject.toml`, `package.json`, project directory structure).
   - Prompts the Discovery Agent (`prompts/spec_discovery.md`) to analyze requirements, identify architectural risks, unstated assumptions, and formulate numbered clarifying questions.

2. **Interactive Rich CLI Dialog**:
   - Presents summary and detected blind spots in styled panels via `rich`.
   - Prompts the user to answer each clarifying question interactively.
   - Submits answers back to the LLM agent to synthesize the authoritative `SPEC.md` complete with acceptance criteria checklists, interfaces, and edge cases.

3. **Approval & Edit Workflow**:
   - Displays rendered preview of `SPEC.md` and target path.
   - Interactive choice prompt: `[A]pprove & Start Dev / [E]dit / [C]ancel`:
     - **[A]pprove**: Writes `SPEC.md` to `repo_path / "SPEC.md"` and returns its content.
     - **[E]dit**: Collects user feedback, re-queries the agent to update `SPEC.md`, and loops back for review.
     - **[C]ancel**: Aborts cleanly by raising `DiscoveryCancelledError`.

---

## Phase 7: Feedback-Loop, Consensus & Auto-Merge

Phase 7 brings together the code review outcomes from Phase 6 and drives final consensus, automated remediation loops, and repository merging (`core/consensus.py`, `core/orchestrator.py`).

1. **Consensus Evaluation (`core/consensus.py`)**:
   - **Fall A (Needs Revision)**: If any review is not `APPROVED` or open `BLOCKER` / `WARNING` findings exist:
     - Formats all findings into structured Markdown documentation with embedded JSON schema.
     - Feeds feedback back into the Dev Agent loop (Phase 4 -> Phase 5 -> Phase 6).
     - Enforces a circuit-breaker limit of maximum 3 re-review attempts.
   - **Fall B (Consensus Approved)**: When all 4 personas have approved and zero blockers or warnings remain:
     - If `auto_merge: true`: executes `gh pr merge --auto --squash` or `glab mr merge --auto --squash`.
     - If `auto_merge: false`: renders an approval table in the terminal and asks:
       `Alle Checks & Reviews bestanden. Merge jetzt ausführen? [y/N]`.
     - Transitions state to `DONE`.

2. **Central State Machine Orchestrator (`core/orchestrator.py`)**:
   - Manages the entire lifecycle from Phase 1 through Phase 7.
   - Persists `WorkflowState` to `state.json` after every step and phase.

3. **CLI Entry-Point (`main.py`)**:
   - Direct requirement input:
     ```bash
     python main.py "Hier ist die Anforderung..."
     ```
   - Input via requirements file:
     ```bash
     python main.py --file requirements.txt
     ```
   - Flags for configuration overrides:
     ```bash
     python main.py --file requirements.txt --auto-merge --config config.yaml
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
