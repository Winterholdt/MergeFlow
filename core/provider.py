"""Modular LLM provider abstraction for MergeFlow.

Supports both CLI-based tools (e.g. `claude -p`, `antigravity run`) and direct
HTTP API calls (OpenAI-compatible / LiteLLM) with JSON schema validation.
"""

from abc import ABC, abstractmethod
import asyncio
import json
import logging
import os
import re
import shutil
from typing import Any, Type, TypeVar

import httpx
from pydantic import BaseModel

from core.config import (
    APIProviderConfig,
    AppConfig,
    CLIProviderConfig,
    load_config,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ProviderError(Exception):
    """Base exception for provider failures."""


class ProviderTimeoutError(ProviderError):
    """Raised when an LLM provider request or command times out."""


class CLIExecutionError(ProviderError):
    """Raised when a CLI command execution fails."""

    def __init__(self, message: str, returncode: int | None = None, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class APIResponseError(ProviderError):
    """Raised when an API endpoint returns an error or invalid response."""

    def __init__(self, message: str, status_code: int | None = None, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class JSONParseError(ProviderError):
    """Raised when output cannot be parsed into the expected JSON schema."""


class BaseLLMProvider(ABC):
    """Abstract base class for all LLM providers."""

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        json_schema: Type[T] | None = None,
    ) -> str | T:
        """Generate a completion for the given prompt.

        Args:
            prompt: User request or instruction.
            system_prompt: System-level guidance or persona.
            json_schema: Optional Pydantic model to enforce structured output.

        Returns:
            String response if json_schema is None, or an instance of json_schema.
        """
        pass

    @staticmethod
    def _extract_and_parse_json(text: str, json_schema: Type[T]) -> T:
        """Extract and validate JSON content from raw LLM output against a schema.

        Args:
            text: Raw output string from LLM.
            json_schema: Target Pydantic model class.

        Returns:
            Validated Pydantic model instance.

        Raises:
            JSONParseError: If JSON cannot be parsed or fails schema validation.
        """
        cleaned = text.strip()

        # Check for markdown code blocks (e.g. ```json ... ``` or ``` ... ```)
        code_fence_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
        fence_matches = re.findall(code_fence_pattern, cleaned, re.IGNORECASE)
        candidates = fence_matches if fence_matches else [cleaned]

        # Also attempt extracting substring between outermost { and } or [ and ]
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            candidates.append(cleaned[first_brace : last_brace + 1])

        last_error: Exception | None = None
        for candidate in candidates:
            cand = candidate.strip()
            if not cand:
                continue
            try:
                # Try direct Pydantic JSON validation
                return json_schema.model_validate_json(cand)
            except Exception as e:
                last_error = e
                # Fallback: parse via json.loads first
                try:
                    loaded = json.loads(cand)
                    if isinstance(loaded, dict):
                        return json_schema.model_validate(loaded)
                except Exception as inner_e:
                    last_error = inner_e

        raise JSONParseError(
            f"Failed to parse or validate output matching schema {json_schema.__name__}. "
            f"Error: {last_error}. Raw response: {text[:200]!r}"
        )

    @staticmethod
    def _format_schema_instruction(json_schema: Type[BaseModel]) -> str:
        """Build schema formatting instructions for LLM prompts."""
        schema_dict = json_schema.model_json_schema()
        return (
            "\n\nIMPORTANT: You must respond ONLY with a valid JSON object matching this schema. "
            "Do not include explanation or Markdown code fences.\n"
            f"Schema:\n{json.dumps(schema_dict, indent=2)}"
        )


class CLIProvider(BaseLLMProvider):
    """Executes local LLM CLI tools (e.g. claude -p, antigravity run) as subprocesses."""

    def __init__(self, config: CLIProviderConfig | None = None):
        self.config = config or CLIProviderConfig()

    async def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        json_schema: Type[T] | None = None,
    ) -> str | T:
        """Execute the CLI subprocess and return output or parsed JSON."""
        resolved_prompt = prompt
        if json_schema is not None:
            resolved_prompt += self._format_schema_instruction(json_schema)

        if system_prompt:
            combined_prompt = f"[SYSTEM INSTRUCTION]\n{system_prompt}\n\n[USER REQUEST]\n{resolved_prompt}"
        else:
            combined_prompt = resolved_prompt

        binary = shutil.which(self.config.binary_path) or self.config.binary_path
        cmd = [binary, *self.config.command_args, combined_prompt]

        # Merge process environment
        exec_env = os.environ.copy()
        exec_env.update(self.config.env)

        logger.debug("Executing CLI command: %s", cmd)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=exec_env,
            )
            stdout_data, stderr_data = await asyncio.wait_for(
                process.communicate(),
                timeout=self.config.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
            raise ProviderTimeoutError(
                f"CLI command '{self.config.binary_path}' timed out after {self.config.timeout_seconds}s"
            ) from exc
        except FileNotFoundError as exc:
            raise CLIExecutionError(
                f"CLI binary '{self.config.binary_path}' was not found in system PATH."
            ) from exc

        stdout_text = stdout_data.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_data.decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            raise CLIExecutionError(
                f"CLI command exited with return code {process.returncode}: {stderr_text}",
                returncode=process.returncode,
                stderr=stderr_text,
            )

        if json_schema is not None:
            return self._extract_and_parse_json(stdout_text, json_schema)

        return stdout_text


class APIProvider(BaseLLMProvider):
    """Direct HTTP API provider supporting OpenAI-compatible chat completion endpoints."""

    def __init__(
        self,
        config: APIProviderConfig | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.config = config or APIProviderConfig()
        self._external_client = client

    async def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        json_schema: Type[T] | None = None,
    ) -> str | T:
        """Call remote chat completion API and return response string or parsed JSON."""
        resolved_prompt = prompt
        if json_schema is not None:
            resolved_prompt += self._format_schema_instruction(json_schema)

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": resolved_prompt})

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
        }
        if json_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        url = f"{self.config.base_url.rstrip('/')}/chat/completions"

        client = self._external_client or httpx.AsyncClient(timeout=self.config.timeout_seconds)
        attempts = 0
        last_exception: Exception | None = None

        try:
            while attempts < max(1, self.config.max_retries):
                attempts += 1
                try:
                    response = await client.post(url, json=payload, headers=headers)
                    if response.status_code >= 400:
                        raise APIResponseError(
                            f"API request failed with HTTP {response.status_code}: {response.text}",
                            status_code=response.status_code,
                            response_text=response.text,
                        )
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    if content is None:
                        content = ""

                    if json_schema is not None:
                        return self._extract_and_parse_json(content, json_schema)

                    return str(content)
                except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
                    last_exception = ProviderTimeoutError(
                        f"API request to '{url}' timed out after {self.config.timeout_seconds}s"
                    )
                except APIResponseError as exc:
                    last_exception = exc
                    # Retry on server errors (5xx)
                    if exc.status_code and exc.status_code < 500:
                        raise
                except Exception as exc:
                    last_exception = exc

                if attempts < self.config.max_retries:
                    await asyncio.sleep(0.5 * attempts)

            if isinstance(last_exception, ProviderError):
                raise last_exception
            raise ProviderError(f"API request failed after {attempts} attempts: {last_exception}")
        finally:
            if self._external_client is None:
                await client.aclose()


def get_provider(
    provider_type: str | None = None,
    config: AppConfig | None = None,
) -> BaseLLMProvider:
    """Factory creating configured LLM provider instances.

    Args:
        provider_type: Provider identifier ('cli', 'api', 'claude', 'antigravity').
                       If None, defaults to config.providers.default_provider.
        config: Application configuration. If None, loaded via load_config().

    Returns:
        Instance of BaseLLMProvider.
    """
    app_config = config or load_config()
    target = (provider_type or app_config.providers.default_provider).lower()

    if target == "cli":
        return CLIProvider(app_config.providers.cli)
    elif target == "claude":
        return CLIProvider(app_config.providers.claude)
    elif target == "antigravity":
        return CLIProvider(app_config.providers.antigravity)
    elif target == "api":
        return APIProvider(app_config.providers.api)
    else:
        raise ValueError(
            f"Unknown provider type: '{provider_type}'. "
            "Supported types are 'cli', 'api', 'claude', 'antigravity'."
        )
