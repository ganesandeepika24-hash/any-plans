"""Daily provider counters (stage 1 step 1, decision 0016).

MeteredLLM sits between the Understand node and the providers. Before each call it
reserves one call in usage_daily, and afterwards it adds the tokens the provider reported.
When a provider's daily calls or tokens are used up, the call is refused with
LLMError("daily_limit") and the node falls back to the next provider, or rests. If the
counter can't be checked, no call is made (principle 1: fail closed on spend).

usage_daily holds counts only: no prompts, replies or users. Days are UTC, as the
providers reset.
"""

import logging
from datetime import UTC, date, datetime
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.understand.llm import Completion, LLMError, ProviderConfig, Usage
from app.understand.store import Store, StoreError

log = logging.getLogger(__name__)

LIMITS_KEY = "provider_limits"
TIMEOUT_S = 5.0


class ProviderLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls_per_day: int = Field(ge=0)
    tokens_per_day: int | None = Field(ge=0)


class UsageError(Exception):
    pass


class UsageCounter(Protocol):
    async def reserve(self, service: str, day: date, limit: ProviderLimit) -> bool:
        """Count one call and return True, or False (counting nothing) at the limit."""
        ...

    async def add_tokens(
        self, service: str, day: date, tokens: int, limit: ProviderLimit
    ) -> None: ...


class SupabaseUsage:
    """reserve_provider_call and add_provider_tokens (migration 016) through PostgREST."""

    def __init__(self, http: httpx.AsyncClient, rest_url: str, service_key: str | None):
        self._http = http
        self._rest = rest_url.rstrip("/")
        self._key = service_key

    async def reserve(self, service: str, day: date, limit: ProviderLimit) -> bool:
        result = await self._rpc(
            "reserve_provider_call",
            {
                "p_service": service,
                "p_day": day.isoformat(),
                "p_call_limit": limit.calls_per_day,
                "p_token_limit": limit.tokens_per_day,
            },
        )
        if not isinstance(result, bool):
            raise UsageError("reserve_provider_call didn't return a boolean")
        return result

    async def add_tokens(self, service: str, day: date, tokens: int, limit: ProviderLimit) -> None:
        await self._rpc(
            "add_provider_tokens",
            {
                "p_service": service,
                "p_day": day.isoformat(),
                "p_tokens": tokens,
                "p_token_limit": limit.tokens_per_day,
            },
        )

    async def _rpc(self, name: str, body: dict[str, Any]) -> Any:
        if not self._key:
            raise UsageError("SUPABASE_SERVICE_KEY is not set")
        try:
            response = await self._http.post(
                f"{self._rest}/rpc/{name}",
                json=body,
                headers={"apikey": self._key, "Authorization": f"Bearer {self._key}"},
                timeout=TIMEOUT_S,
            )
            response.raise_for_status()
            return response.json() if response.content else None
        except (httpx.HTTPError, ValueError) as exc:
            raise UsageError(f"{name} failed: {type(exc).__name__}") from exc


class MeteredLLM:
    def __init__(self, inner, store: Store, counter: UsageCounter, clock=lambda: datetime.now(UTC)):
        self._inner = inner
        self._store = store
        self._counter = counter
        self._clock = clock

    async def complete(
        self, config: ProviderConfig, system: str, user: str, schema: dict[str, Any] | None
    ) -> Completion:
        provider = config.provider
        day = self._clock().astimezone(UTC).date()
        try:
            limits = (await self._store.config((LIMITS_KEY,)))[LIMITS_KEY].value
            limit = ProviderLimit.model_validate(limits[provider])
            reserved = await self._counter.reserve(provider, day, limit)
        except (StoreError, UsageError, ValidationError, KeyError, TypeError) as exc:
            raise LLMError("usage_unavailable", f"daily counter unavailable: {exc}") from exc
        if not reserved:
            raise LLMError("daily_limit", f"{provider}'s daily limit is used up")

        try:
            completion = await self._inner.complete(config, system, user, schema)
        except LLMError as exc:
            await self._add_tokens(provider, day, exc.usage, limit)
            raise
        await self._add_tokens(provider, day, completion.usage, limit)
        return completion

    async def _add_tokens(self, provider: str, day: date, usage: Usage, limit: ProviderLimit):
        tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0)
        if not tokens:
            return
        try:
            await self._counter.add_tokens(provider, day, tokens, limit)
        except UsageError as exc:
            # The call already happened. Losing its tokens only makes the token limit bite
            # later; the call itself was counted when it was reserved.
            log.warning("%s tokens not counted: %s", provider, exc)
