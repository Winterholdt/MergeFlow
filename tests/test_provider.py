"""Unit tests for modular LLM provider implementations."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from pydantic import BaseModel

from core.config import AppConfig, CLIProviderConfig, APIProviderConfig
from core.provider import (
    APIProvider,
    APIResponseError,
    CLIExecutionError,
    CLIProvider,
    JSONParseError,
    ProviderTimeoutError,
    get_provider,
)


class SampleModel(BaseModel):
    summary: str
    score: int


# ============================================================================
# JSON Parsing and Extraction Tests
# ============================================================================

def test_extract_and_parse_json_pure():
    """Verify parsing pure JSON."""
    raw = '{"summary": "All good", "score": 95}'
    model = CLIProvider._extract_and_parse_json(raw, SampleModel)
    assert model.summary == "All good"
    assert model.score == 95


def test_extract_and_parse_json_markdown_fence():
    """Verify extracting and parsing JSON wrapped in markdown code fences."""
    raw = """Here is the result:
```json
{
  "summary": "Verified",
  "score": 88
}
```
Thanks!"""
    model = CLIProvider._extract_and_parse_json(raw, SampleModel)
    assert model.summary == "Verified"
    assert model.score == 88


def test_extract_and_parse_json_invalid():
    """Verify invalid JSON raises JSONParseError."""
    raw = "Not a json response at all"
    with pytest.raises(JSONParseError):
        CLIProvider._extract_and_parse_json(raw, SampleModel)


# ============================================================================
# CLIProvider Tests
# ============================================================================

@pytest.mark.asyncio
async def test_cli_provider_complete_text():
    """Verify CLIProvider successful text output."""
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (b"Hello from CLI\n", b"")
    mock_process.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        config = CLIProviderConfig(binary_path="claude", command_args=["-p"], timeout_seconds=10.0)
        provider = CLIProvider(config)

        result = await provider.complete(prompt="Say hello", system_prompt="Be polite")

        assert result == "Hello from CLI"
        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert "claude" in args[0]
        assert args[1] == "-p"
        assert "[SYSTEM INSTRUCTION]\nBe polite\n\n[USER REQUEST]\nSay hello" in args[2]


@pytest.mark.asyncio
async def test_cli_provider_complete_json_schema():
    """Verify CLIProvider structured output with Pydantic model validation."""
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (
        b'```json\n{"summary": "Passed", "score": 100}\n```',
        b"",
    )
    mock_process.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        provider = CLIProvider()
        result = await provider.complete(prompt="Generate status", json_schema=SampleModel)

        assert isinstance(result, SampleModel)
        assert result.summary == "Passed"
        assert result.score == 100


@pytest.mark.asyncio
async def test_cli_provider_error_exit_code():
    """Verify CLIProvider raises CLIExecutionError on non-zero exit."""
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (b"", b"Authentication error")
    mock_process.returncode = 1

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        provider = CLIProvider()
        with pytest.raises(CLIExecutionError) as exc_info:
            await provider.complete(prompt="Test")
        assert "Authentication error" in str(exc_info.value)
        assert exc_info.value.returncode == 1


@pytest.mark.asyncio
async def test_cli_provider_timeout():
    """Verify CLIProvider handles timeout properly."""
    mock_process = AsyncMock()
    mock_process.communicate.side_effect = asyncio.TimeoutError()
    mock_process.kill = MagicMock()
    mock_process.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        provider = CLIProvider(CLIProviderConfig(timeout_seconds=0.1))
        with pytest.raises(ProviderTimeoutError):
            await provider.complete(prompt="Sleep forever")
        mock_process.kill.assert_called_once()


# ============================================================================
# APIProvider Tests
# ============================================================================

@pytest.mark.asyncio
async def test_api_provider_complete_text():
    """Verify APIProvider parses successful chat completion."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Response from API"}}]
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    provider = APIProvider(
        config=APIProviderConfig(base_url="https://api.openai.com/v1", api_key="sk-test", model="gpt-4o"),
        client=mock_client,
    )

    result = await provider.complete(prompt="Hello API", system_prompt="You are helpful")
    assert result == "Response from API"

    mock_client.post.assert_called_once()
    call_args, call_kwargs = mock_client.post.call_args
    assert call_args[0] == "https://api.openai.com/v1/chat/completions"
    assert call_kwargs["headers"]["Authorization"] == "Bearer sk-test"
    payload = call_kwargs["json"]
    assert payload["model"] == "gpt-4o"
    assert payload["messages"][0] == {"role": "system", "content": "You are helpful"}
    assert payload["messages"][1] == {"role": "user", "content": "Hello API"}


@pytest.mark.asyncio
async def test_api_provider_complete_json_schema():
    """Verify APIProvider validates structured JSON schema."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"summary": "API OK", "score": 90}'}}]
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    provider = APIProvider(client=mock_client)
    result = await provider.complete(prompt="Score this", json_schema=SampleModel)

    assert isinstance(result, SampleModel)
    assert result.summary == "API OK"
    assert result.score == 90


@pytest.mark.asyncio
async def test_api_provider_http_error():
    """Verify APIProvider raises APIResponseError on client error."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    provider = APIProvider(client=mock_client)
    with pytest.raises(APIResponseError) as exc_info:
        await provider.complete(prompt="Test")
    assert exc_info.value.status_code == 401


# ============================================================================
# Factory get_provider Tests
# ============================================================================

def test_get_provider_factory():
    """Verify factory returns appropriate provider types according to config."""
    config = AppConfig()

    cli_provider = get_provider("cli", config)
    assert isinstance(cli_provider, CLIProvider)

    claude_provider = get_provider("claude", config)
    assert isinstance(claude_provider, CLIProvider)
    assert claude_provider.config.binary_path == "claude"

    antigravity_provider = get_provider("antigravity", config)
    assert isinstance(antigravity_provider, CLIProvider)
    assert antigravity_provider.config.binary_path == "antigravity"
    assert antigravity_provider.config.command_args == ["run"]

    api_provider = get_provider("api", config)
    assert isinstance(api_provider, APIProvider)

    # Test passing AppConfig as first argument or keyword argument
    default_provider_pos = get_provider(config)
    assert isinstance(default_provider_pos, CLIProvider)

    default_provider_kw = get_provider(config=config)
    assert isinstance(default_provider_kw, CLIProvider)

    reversed_args_provider = get_provider(config, "api")
    assert isinstance(reversed_args_provider, APIProvider)

    agy_provider = get_provider("agy", config)
    assert isinstance(agy_provider, CLIProvider)
    assert agy_provider.config.binary_path == "antigravity"

    with pytest.raises(ValueError):
        get_provider("unsupported_type", config)
