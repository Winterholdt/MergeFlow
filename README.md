# MergeFlow: Autonomous Multi-Agent Orchestrator

MergeFlow is a modular, autonomous multi-agent orchestration framework designed for automated software development lifecycle tasks, from requirements discovery and spec generation to coding, CI remediation, and automated peer reviews.

## Phase 1 Architecture

Phase 1 establishes the core scaffolding, configuration management, state models, and model-agnostic LLM provider abstraction.

```
MergeFlow/
├── config.yaml                # Example configuration (models, timeouts, retries, CLI binaries)
├── pyproject.toml             # Project metadata, dependencies, and test settings
├── requirements.txt           # Pip dependencies
├── core/
│   ├── __init__.py            # Package exports
│   ├── config.py              # YAML and Pydantic Settings configuration loader
│   ├── models.py              # Domain & workflow state models (Pydantic v2)
│   └── provider.py            # LLM provider abstraction (CLI & API with JSON schema support)
└── tests/
    ├── test_config.py         # Config loading and validation test suite
    ├── test_models.py         # Workflow state, findings, and spec test suite
    └── test_provider.py       # CLI and API provider test suite (mocked)
```

### 1. Configuration Management (`core/config.py`)
- Powered by `pydantic-settings` with YAML file loader and environment variable override support (`MERGEFLOW_` prefix).
- Manages CLI tool paths (e.g. `claude -p`, `antigravity run`), timeouts, API keys, endpoints, and workflow retry thresholds.
- Can be loaded with `load_config("config.yaml")` or initialized with defaults.

### 2. State & Domain Models (`core/models.py`)
- **`WorkflowState`**: Tracks task execution across phases (`SPEC_DISCOVERY`, `CODING`, `CI_LOOP`, `REVIEW`, `DONE`, `FAILED`), branch information, CI retry counts, review retry counts, and findings.
- **`Finding`**: Structured representation of bugs, security vulnerabilities, or quality recommendations with severity levels and file/line references.
- **`ReviewResult`**: Feedback from code reviewer agents including approval status and findings.
- **`Specification`**: Extracted technical requirements with acceptance criteria.

### 3. LLM Provider Abstraction (`core/provider.py`)
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
