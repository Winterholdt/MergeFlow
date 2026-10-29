"""Test suite for Phase 3: Requirements Discovery & Specification Loop."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from core.discovery import (
    DiscoveryAnalysis,
    DiscoveryCancelledError,
    DiscoveryError,
    _clean_markdown_fence,
    _parse_discovery_analysis,
    gather_repo_context,
    load_discovery_prompt,
    run_discovery_phase,
)
from core.provider import BaseLLMProvider


class MockLLMProvider(BaseLLMProvider):
    """Configurable mock provider for testing discovery workflows."""

    def __init__(self, responses: list[str | DiscoveryAnalysis] | None = None):
        self.responses = list(responses or [])
        self.call_history: list[dict[str, object]] = []

    async def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        json_schema: type | None = None,
    ) -> str | DiscoveryAnalysis:
        self.call_history.append({
            "prompt": prompt,
            "system_prompt": system_prompt,
            "json_schema": json_schema,
        })
        if not self.responses:
            raise RuntimeError("No mock responses remaining")
        return self.responses.pop(0)


def test_gather_repo_context(tmp_path: Path):
    """Test gathering repository context from directory structure and files."""
    # Setup dummy repo
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test-pkg"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test Repo\nThis is a test.\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')", encoding="utf-8")

    context = gather_repo_context(tmp_path)
    assert "pyproject.toml" in context
    assert "README.md" in context
    assert "test-pkg" in context
    assert ".git" not in context  # Should be filtered from top-level listing


def test_gather_repo_context_nonexistent(tmp_path: Path):
    """Test gathering context on non-existent directory."""
    non_dir = tmp_path / "does_not_exist"
    context = gather_repo_context(non_dir)
    assert "not an accessible directory" in context


def test_gather_repo_context_truncation(tmp_path: Path):
    """Test truncation of very large manifest files."""
    long_content = "x" * 5000
    (tmp_path / "README.md").write_text(long_content, encoding="utf-8")
    context = gather_repo_context(tmp_path, max_file_chars=100)
    assert "... [truncated at 100 chars]" in context


def test_load_discovery_prompt(tmp_path: Path):
    """Test loading the discovery prompt from file or fallback."""
    # Test fallback
    fallback = load_discovery_prompt(tmp_path / "missing.md")
    assert "Discovery Agent" in fallback

    # Test file reading
    custom_prompt_file = tmp_path / "custom_prompt.md"
    custom_prompt_file.write_text("Custom Discovery Persona", encoding="utf-8")
    loaded = load_discovery_prompt(custom_prompt_file)
    assert loaded == "Custom Discovery Persona"


def test_clean_markdown_fence():
    """Test stripping markdown wrapper fences."""
    wrapped = "```markdown\n# Spec\nSome content\n```"
    assert _clean_markdown_fence(wrapped) == "# Spec\nSome content"

    wrapped_no_lang = "```\n# Spec\nSome content\n```"
    assert _clean_markdown_fence(wrapped_no_lang) == "# Spec\nSome content"

    plain = "# Spec\n```python\nprint(1)\n```"
    assert _clean_markdown_fence(plain) == plain


def test_parse_discovery_analysis():
    """Test parsing DiscoveryAnalysis from models, dicts, JSON strings, and text."""
    # 1. From model
    m = DiscoveryAnalysis(summary="sum", blind_spots=["b1"], questions=["q1"])
    assert _parse_discovery_analysis(m) == m

    # 2. From dict
    d = {"summary": "sum", "blind_spots": ["b1"], "questions": ["q1"]}
    parsed_d = _parse_discovery_analysis(d)
    assert parsed_d.summary == "sum"

    # 3. From JSON string
    json_str = '{"summary": "from json", "blind_spots": ["bs"], "questions": ["q1?"]}'
    parsed_json = _parse_discovery_analysis(json_str)
    assert parsed_json.summary == "from json"
    assert parsed_json.questions == ["q1?"]

    # 4. From markdown fenced JSON
    fence_json = '```json\n{"summary": "fenced", "blind_spots": [], "questions": ["q?"]}\n```'
    parsed_fence = _parse_discovery_analysis(fence_json)
    assert parsed_fence.summary == "fenced"

    # 5. From plain text heuristic
    plain_text = (
        "This is an analysis summary.\n"
        "- Potential race condition\n"
        "- Missing validation\n"
        "1. How should auth be handled?\n"
        "2. What database is used?\n"
    )
    parsed_text = _parse_discovery_analysis(plain_text)
    assert "analysis summary" in parsed_text.summary
    assert len(parsed_text.blind_spots) == 2
    assert len(parsed_text.questions) == 2


@pytest.mark.asyncio
async def test_run_discovery_phase_approve(tmp_path: Path):
    """Test successful discovery phase ending with [A]pprove."""
    analysis = DiscoveryAnalysis(
        summary="Add OAuth2 authentication",
        blind_spots=["Token expiration not specified", "Refresh token storage"],
        questions=[
            "Which OAuth2 providers should be supported (Google, GitHub)?",
            "Where should tokens be stored (Redis, PostgreSQL)?",
        ],
    )
    spec_md = "# SPEC: OAuth2 Auth\n\n## 4. Acceptance Criteria\n- [ ] Supports GitHub"

    mock_provider = MockLLMProvider(responses=[analysis, spec_md])

    user_inputs = [
        "GitHub and Google",  # Answer to Q1
        "PostgreSQL",        # Answer to Q2
        "a",                 # [A]pprove
    ]
    input_func = lambda _: user_inputs.pop(0)
    console = Console(record=True, width=100)

    result_spec = await run_discovery_phase(
        raw_requirements="We need OAuth2 login for users.",
        provider=mock_provider,
        repo_path=tmp_path,
        console=console,
        input_func=input_func,
    )

    assert result_spec == spec_md
    saved_file = tmp_path / "SPEC.md"
    assert saved_file.is_file()
    assert saved_file.read_text(encoding="utf-8") == spec_md
    assert len(mock_provider.call_history) == 2


@pytest.mark.asyncio
async def test_run_discovery_phase_edit_loop(tmp_path: Path):
    """Test discovery phase with an [E]dit cycle before [A]pprove."""
    analysis = DiscoveryAnalysis(
        summary="Build CLI tool",
        blind_spots=["Output format not defined"],
        questions=["Should output support JSON?"],
    )
    initial_spec = "# SPEC: CLI Tool v1"
    revised_spec = "# SPEC: CLI Tool v2\n- [ ] JSON supported"

    mock_provider = MockLLMProvider(responses=[analysis, initial_spec, revised_spec])

    user_inputs = [
        "Yes, JSON is required",  # Answer Q1
        "e",                      # [E]dit
        "Please add JSON output criteria",  # Feedback
        "approve",                # [A]pprove
    ]
    input_func = lambda _: user_inputs.pop(0)
    console = Console(record=True, width=100)

    result_spec = await run_discovery_phase(
        raw_requirements="Build a CLI tool",
        provider=mock_provider,
        repo_path=tmp_path,
        console=console,
        input_func=input_func,
    )

    assert result_spec == revised_spec
    saved_file = tmp_path / "SPEC.md"
    assert saved_file.read_text(encoding="utf-8") == revised_spec
    assert len(mock_provider.call_history) == 3


@pytest.mark.asyncio
async def test_run_discovery_phase_cancel(tmp_path: Path):
    """Test cancellation of discovery phase via [C]ancel."""
    analysis = DiscoveryAnalysis(
        summary="Build payment gateway",
        blind_spots=["Stripe API keys missing"],
        questions=["Do we have Stripe test keys?"],
    )
    spec_md = "# SPEC: Payments"

    mock_provider = MockLLMProvider(responses=[analysis, spec_md])

    user_inputs = [
        "Not yet",  # Answer Q1
        "c",        # [C]ancel
    ]
    input_func = lambda _: user_inputs.pop(0)
    console = Console(record=True, width=100)

    with pytest.raises(DiscoveryCancelledError, match="cancelled by user"):
        await run_discovery_phase(
            raw_requirements="Integrate payment gateway",
            provider=mock_provider,
            repo_path=tmp_path,
            console=console,
            input_func=input_func,
        )

    # SPEC.md must NOT have been saved
    assert not (tmp_path / "SPEC.md").exists()


@pytest.mark.asyncio
async def test_run_discovery_phase_empty_requirements(tmp_path: Path):
    """Test that empty raw requirements raises DiscoveryError."""
    mock_provider = MockLLMProvider()
    with pytest.raises(DiscoveryError, match="cannot be empty"):
        await run_discovery_phase(
            raw_requirements="   ",
            provider=mock_provider,
            repo_path=tmp_path,
        )


@pytest.mark.asyncio
async def test_run_discovery_phase_no_questions(tmp_path: Path):
    """Test flow when no clarifying questions are identified."""
    analysis = DiscoveryAnalysis(
        summary="Clear and complete task",
        blind_spots=[],
        questions=[],
    )
    spec_md = "# SPEC: Quick Task"

    mock_provider = MockLLMProvider(responses=[analysis, spec_md])
    user_inputs = ["a"]  # Directly approve
    input_func = lambda _: user_inputs.pop(0)
    console = Console(record=True, width=100)

    result_spec = await run_discovery_phase(
        raw_requirements="Well-defined requirement without ambiguity",
        provider=mock_provider,
        repo_path=tmp_path,
        console=console,
        input_func=input_func,
    )

    assert result_spec == spec_md
    assert (tmp_path / "SPEC.md").is_file()


@pytest.mark.asyncio
async def test_run_discovery_phase_provider_failure(tmp_path: Path):
    """Test that provider failure during analysis raises DiscoveryError."""
    mock_provider = MockLLMProvider(responses=[])  # Will raise RuntimeError

    with pytest.raises(DiscoveryError, match="Discovery phase failed during LLM analysis"):
        await run_discovery_phase(
            raw_requirements="Some requirements",
            provider=mock_provider,
            repo_path=tmp_path,
        )


@pytest.mark.asyncio
async def test_run_discovery_phase_spec_gen_failure(tmp_path: Path):
    """Test that provider failure during spec generation raises DiscoveryError."""
    analysis = DiscoveryAnalysis(summary="Summary", questions=["Q1?"])
    # First call succeeds, second call fails
    mock_provider = MockLLMProvider(responses=[analysis])
    user_inputs = ["Answer 1"]
    input_func = lambda _: user_inputs.pop(0)

    with pytest.raises(DiscoveryError, match="Failed to generate SPEC.md"):
        await run_discovery_phase(
            raw_requirements="Some requirements",
            provider=mock_provider,
            repo_path=tmp_path,
            input_func=input_func,
        )


@pytest.mark.asyncio
async def test_run_discovery_phase_invalid_then_approve(tmp_path: Path):
    """Test entering invalid choices before approving."""
    analysis = DiscoveryAnalysis(summary="Summary", questions=[])
    spec_md = "# SPEC: Valid"

    mock_provider = MockLLMProvider(responses=[analysis, spec_md])
    user_inputs = [
        "invalid_choice",  # Invalid choice
        "a",               # Then approve
    ]
    input_func = lambda _: user_inputs.pop(0)
    console = Console(record=True, width=100)

    result = await run_discovery_phase(
        raw_requirements="Valid requirements",
        provider=mock_provider,
        repo_path=tmp_path,
        console=console,
        input_func=input_func,
    )
    assert result == spec_md
    assert (tmp_path / "SPEC.md").is_file()

