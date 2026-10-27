"""LLM clients for the Understand node (decision 0002, 0012).

Both providers are called through their OpenAI-compatible chat completions endpoints.
Models and settings come from the llm_understand config; keys come from settings and
never appear in config, traces or errors. Every failure is an LLMError with an outcome
name, so the chain can fall back and the trace can say why.
"""

import json
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CLOUDFLARE_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"

Outcome = Literal[
    "ok",
    "no_key",
    "timeout",
    "network",
    "http_429",
    "http_5xx",
    "http_4xx",
    "empty",
    "truncated",
    "invalid_json",
    "schema",
]


class ProviderConfig(BaseModel):
    """One entry in llm_understand.providers."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["groq", "cloudflare"]
    model: str = Field(min_length=1)
    structured: bool
    timeout_s: float = Field(gt=0, le=60)
    max_tokens: int = Field(gt=0, le=8192)
    temperature: float = Field(ge=0, le=2)
    reasoning_effort: Literal["low", "medium", "high"] | None = None


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderConfig] = Field(min_length=1)


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage
    latency_ms: int


class LLMError(Exception):
    def __init__(self, outcome: Outcome, message: str, usage: Usage | None = None):
        super().__init__(message)
        self.outcome = outcome
        self.usage = usage or Usage()


class LLMClient(Protocol):
    async def complete(
        self,
        config: ProviderConfig,
        system: str,
        user: str,
        schema: dict[str, Any] | None,
    ) -> Completion:
        """Return the reply text, or raise LLMError. `schema` is set when the provider
        is configured as structured."""
        ...


class OpenAICompatibleClient:
    """Groq and Cloudflare Workers AI. Both take OpenAI chat completions requests."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        groq_api_key: str | None,
        cloudflare_api_token: str | None,
        cloudflare_account_id: str | None,
    ):
        self._http = http
        self._groq_key = groq_api_key
        self._cf_token = cloudflare_api_token
        self._cf_account = cloudflare_account_id

    async def complete(
        self,
        config: ProviderConfig,
        system: str,
        user: str,
        schema: dict[str, Any] | None,
    ) -> Completion:
        url, key = self._endpoint(config)
        body: dict[str, Any] = {
            "model": config.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": config.temperature,
        }
        if config.provider == "groq":
            body["max_completion_tokens"] = config.max_tokens
        else:
            body["max_tokens"] = config.max_tokens
        if config.reasoning_effort is not None:
            body["reasoning_effort"] = config.reasoning_effort
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "understood_query", "strict": True, "schema": schema},
            }

        started = time.monotonic()
        try:
            response = await self._http.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {key}"},
                timeout=config.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise LLMError("timeout", f"no reply within {config.timeout_s:g} s") from exc
        except httpx.TransportError as exc:
            raise LLMError("network", type(exc).__name__) from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise LLMError("http_429", f"rate limited (retry-after {retry_after})")
        if response.status_code >= 500:
            raise LLMError("http_5xx", f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise LLMError("http_4xx", f"HTTP {response.status_code}")

        try:
            payload = response.json()
            choice = payload["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("invalid_json", "response isn't a chat completion") from exc
        usage = _usage(payload.get("usage"))
        if choice.get("finish_reason") == "length":
            raise LLMError("truncated", "reply cut off at max_tokens", usage)
        text = (choice.get("message") or {}).get("content")
        if not isinstance(text, str) or not text.strip():
            raise LLMError("empty", "no reply text", usage)
        return Completion(text=text, usage=usage, latency_ms=latency_ms)

    def _endpoint(self, config: ProviderConfig) -> tuple[str, str]:
        if config.provider == "groq":
            if not self._groq_key:
                raise LLMError("no_key", "GROQ_API_KEY is not set")
            return GROQ_URL, self._groq_key
        if not (self._cf_token and self._cf_account):
            raise LLMError("no_key", "CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID is not set")
        return CLOUDFLARE_URL.format(account_id=self._cf_account), self._cf_token


def _usage(raw: Any) -> Usage:
    if not isinstance(raw, dict):
        return Usage()
    return Usage(raw.get("prompt_tokens"), raw.get("completion_tokens"))


def schema_instruction(schema: dict[str, Any]) -> str:
    """Appended to the system prompt for providers without structured output."""
    return (
        "\n\nReturn only a JSON object that matches this JSON Schema, with no other text:\n"
        + json.dumps(schema, separators=(",", ":"))
    )
