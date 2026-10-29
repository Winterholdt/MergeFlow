# MergeFlow: Autonomous Multi-Agent Orchestrator

MergeFlow is a modular, autonomous multi-agent orchestration framework designed for automated software development lifecycle tasks. It guides tasks from initial requirements discovery and specification generation to autonomous coding, self-healing CI remediation, multi-persona peer reviews, and automated pull request merging.

---

## Workflow Overview

MergeFlow coordinates autonomous agents through a structured 7-phase pipeline:

1. **Requirements Discovery & Spec Generation**: Interactive CLI dialog that identifies architectural risks and blind spots, asks clarifying questions, and produces a validated `SPEC.md`.
2. **Command Gatekeeper & Sandbox**: Strict security engine combining fast-path allowlists, regex blocklists, and LLM safety audits to safely execute commands autonomously.
3. **Autonomous Developer Loop**: Iteratively implements code, creates targeted commits, and runs local verification tests.
4. **CI Self-Healing**: Monitors remote CI pipelines, extracts failure logs, and automatically generates fixes.
5. **Multi-Persona Code Review**: Evaluates changes against four independent review personas:
   - **Functional**: Requirements compliance & edge-case coverage.
   - **Architecture**: SOLID principles, modularity & code quality.
   - **Security**: OWASP Top 10 vulnerabilities & data validation.
   - **QA / Testing**: Unit/integration test quality & assertion robustness.
6. **Consensus & Remediation**: Aggregates findings and feeds actionable feedback back into the developer loop until all blockers are resolved (with circuit-breaker protection).
7. **Auto-Merge**: Automatically merges passing pull requests via GitHub (`gh`) or GitLab (`glab`), or requests manual confirmation.

---

## Prerequisites

- **Python**: 3.11 or higher
- **Git**: Installed and available in your `PATH`
- **VCS CLI** (Optional, required for PR creation & auto-merge):
  - [GitHub CLI (`gh`)](https://cli.github.com/) or [GitLab CLI (`glab`)](https://gitlab.com/gitlab-org/cli)
- **LLM Provider**:
  - Local CLI agent (e.g. Claude Code `claude`, Antigravity `antigravity`), or
  - OpenAI-compatible API key (`OPENAI_API_KEY`)

---

## Installation

### 1. Clone Repository & Setup Virtual Environment

```bash
# Clone repository
git clone <repo-url>
cd ai-orchrestator

# Create virtual environment
python -m venv .venv

# Activate virtual environment:
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On Windows (cmd):
.venv\Scripts\activate.bat
# On Linux / macOS:
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt

# Or install in editable mode including development & test tools:
pip install -e ".[dev]"
```

---

## Configuration

MergeFlow loads settings from `config.yaml` with environment variable override support (prefixed with `MERGEFLOW_`).

Key options in `config.yaml`:

```yaml
environment: "development"
log_level: "INFO"

providers:
  default_provider: "cli"  # Options: "cli", "api", "claude", "antigravity"

  # Option A: Local CLI Agent (Claude Code / Antigravity)
  cli:
    binary_path: "claude"
    command_args: ["-p"]
    timeout_seconds: 300.0

  # Option B: Direct API (OpenAI / LiteLLM / Ollama)
  api:
    base_url: "https://api.openai.com/v1"
    api_key: null  # Set here or via OPENAI_API_KEY environment variable
    model: "gpt-4o"
    timeout_seconds: 60.0

workflow:
  ci_max_retries: 3
  review_max_retries: 3
  default_branch: "main"
  auto_merge: false
```

---

## How to Run

### 1. Direct Task Prompt
Pass task requirements directly as a command-line string:
```bash
python main.py "Implement user authentication with JWT and refresh tokens"
```

### 2. Requirements from File
Load a task description or spec from a text or markdown file:
```bash
python main.py --file requirements.txt
# or
python main.py -f docs/task.md
```

### 3. Common CLI Flags

| Flag | Description | Default |
| --- | --- | --- |
| `requirements` | Positional prompt string describing the task | `None` |
| `-f`, `--file <path>` | Path to requirements file | `None` |
| `-p`, `--provider <type>` | Override LLM provider (`cli`, `api`, `claude`, `antigravity`) | From config |
| `-c`, `--config <path>` | Path to custom YAML configuration file | `config.yaml` |
| `-r`, `--repo <path>` | Target repository root directory | `.` |
| `--auto-merge` | Automatically squash-merge PR when all checks and reviews pass | Interactive prompt |
| `--no-auto-merge` | Require explicit terminal confirmation before merging | Active |
| `--resume [TASK_ID]` | Resume an interrupted workflow from state persistence file | `None` |
| `-s`, `--state <path>` | Workflow state file location | `.orchestrator/state.json` |

### 4. Resuming Interrupted Workflows
If a run is aborted or fails, progress is saved in `.orchestrator/state.json`. Resume where you left off with:
```bash
python main.py --resume
```

---

## Testing

Execute the test suite with `pytest`:

```bash
# Run all tests
pytest

# Run tests with verbose output
pytest -v
```

---

## Project Structure

```
ai-orchrestator/
├── config.yaml          # Global configuration (providers, timeouts, retry limits)
├── main.py              # CLI entry point (Phases 1-7)
├── prompts/             # Agent persona prompts (discovery, developer, CI, review)
├── core/                # Core orchestration & state management
│   ├── config.py        # Pydantic Settings configuration loader
│   ├── consensus.py     # Multi-persona review aggregation & consensus logic
│   ├── dev_loop.py      # Autonomous developer implementation cycle
│   ├── discovery.py     # Interactive requirement engineering & SPEC generation
│   ├── healing_loop.py  # Self-healing CI build/test resolution
│   ├── models.py        # Pydantic v2 workflow state & domain models
│   ├── orchestrator.py  # Central state machine orchestrating phases 1 to 7
│   ├── provider.py      # LLM provider abstractions (CLI & API)
│   └── reviewer.py      # 4-persona code review engine
├── security/            # Execution sandbox & gatekeeping
│   ├── allow_block_list.py # Allowed tools & blocked destructive commands
│   ├── gatekeeper.py    # Command security analyzer & LLM audit fallback
│   └── shell_runner.py  # Subprocess execution with streaming and timeouts
├── vcs/                 # Git & remote VCS integration (GitHub / GitLab)
└── tests/               # Unit and integration test suites
```
