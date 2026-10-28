"""Daily provider counters (stage 1 step 1, decision 0016). The counter here keeps the
same rules as migration 016's functions; db/tests checks the functions themselves."""

import json
from datetime import date, datetime

import httpx
import pytest

from app.understand.llm import LLMError, Usage
from app.understand.node import Understander
from app.usage import MeteredLLM, ProviderLimit, SupabaseUsage, UsageError
from tests.understand_support import (
    FakeLLM,
    FakeStore,
    RecordingTracer,
    entries,
    migration_config,
    reply,
    when,
)

pytestmark = pytest.mark.anyio

NOW = datetime.fromisoformat("2026-10-28T23:30:00+00:00")
DAY = date(2026, 10, 28)
GOOD = reply(keywords="jazz", categories=["Music"], when=when("tonight"))


class FakeCounter:
    """usage_daily in memory, with reserve_provider_call's and add_provider_tokens' rules."""

    def __init__(self, calls=None, tokens=None, fail=False):
        self.calls: dict[tuple[str, date], int] = calls or {}
        self.tokens: dict[tuple[str, date], int] = tokens or {}
        self.fail = fail

    async def reserve(self, service, day, limit):
        if self.fail:
            raise UsageError("reserve_provider_call failed: ConnectError")
        if (
            limit.tokens_per_day is not None
            and self.tokens.get((service, day), 0) >= limit.tokens_per_day
        ):
            return False
        if self.calls.get((service, day), 0) >= limit.calls_per_day:
            return False
        self.calls[(service, day)] = self.calls.get((service, day), 0) + 1
        return True

    async def add_tokens(self, service, day, tokens, limit):
        if self.fail:
            raise UsageError("add_provider_tokens failed: ConnectError")
        self.tokens[(service, day)] = self.tokens.get((service, day), 0) + tokens


def node(llm, counter, store=None):
    store = store or FakeStore()
    metered = MeteredLLM(llm, store, counter, clock=lambda: NOW)
    return Understander(store, metered, RecordingTracer(), clock=lambda: NOW)


def test_the_limits_match_the_providers_published_free_tiers():
    limits = migration_config()["provider_limits"]
    assert limits == {
        "groq": {"calls_per_day": 1000, "tokens_per_day": 200000},
        "cloudflare": {"calls_per_day": 200, "tokens_per_day": None},
    }


async def test_each_call_is_counted_with_its_tokens():
    counter = FakeCounter()
    result = await node(FakeLLM(groq=[GOOD]), counter).run("jazz tonight")
    assert result.status == "ok"
    assert counter.calls == {("groq", DAY): 1}
    assert counter.tokens == {("groq", DAY): 160}  # the fake's 120 in + 40 out


async def test_at_groqs_call_limit_it_falls_back_to_cloudflare_without_calling_groq():
    llm = FakeLLM(groq=[GOOD], cloudflare=[GOOD])
    counter = FakeCounter(calls={("groq", DAY): 1000})
    result = await node(llm, counter).run("jazz tonight")
    assert (result.status, result.provider) == ("ok", "cloudflare")
    assert [c["provider"] for c in llm.calls] == ["cloudflare"]
    assert [(a.provider, a.outcome) for a in result.attempts] == [
        ("groq", "daily_limit"),
        ("cloudflare", "ok"),
    ]
    assert counter.calls[("groq", DAY)] == 1000  # a refused call isn't counted


async def test_at_groqs_token_limit_it_stops_calling_groq():
    llm = FakeLLM(groq=[GOOD], cloudflare=[GOOD])
    counter = FakeCounter(tokens={("groq", DAY): 200000})
    result = await node(llm, counter).run("jazz tonight")
    assert result.provider == "cloudflare" and [c["provider"] for c in llm.calls] == ["cloudflare"]


async def test_with_every_provider_at_its_limit_no_call_is_made_and_it_rests():
    llm = FakeLLM(groq=[GOOD], cloudflare=[GOOD])
    counter = FakeCounter(calls={("groq", DAY): 1000, ("cloudflare", DAY): 200})
    result = await node(llm, counter).run("jazz tonight")
    assert result.status == "ai_resting" and llm.calls == []


async def test_the_limit_is_reached_mid_day_and_the_last_call_is_the_limit():
    store = FakeStore(entries(provider_limits={
        "groq": {"calls_per_day": 2, "tokens_per_day": None},
        "cloudflare": {"calls_per_day": 0, "tokens_per_day": None},
    }))  # fmt: skip
    llm = FakeLLM(groq=[GOOD, GOOD, GOOD], cloudflare=[])
    counter = FakeCounter()
    statuses = [(await node(llm, counter, store).run("jazz")).status for _ in range(3)]
    assert statuses == ["ok", "ok", "ai_resting"] and len(llm.calls) == 2


async def test_a_new_utc_day_starts_from_zero():
    counter = FakeCounter(calls={("groq", date(2026, 10, 27)): 1000})
    result = await node(FakeLLM(groq=[GOOD]), counter).run("jazz")
    assert result.provider == "groq"


async def test_if_the_counter_cant_be_checked_no_call_is_made():
    llm = FakeLLM(groq=[GOOD], cloudflare=[GOOD])
    result = await node(llm, FakeCounter(fail=True)).run("jazz")
    assert result.status == "ai_resting" and llm.calls == []
    assert {a.outcome for a in result.attempts} == {"usage_unavailable"}


async def test_missing_limits_config_means_no_call():
    store = FakeStore(entries())
    del store.entries["provider_limits"]
    llm = FakeLLM(groq=[GOOD], cloudflare=[GOOD])
    result = await node(llm, FakeCounter(), store).run("jazz")
    assert result.status == "ai_resting" and llm.calls == []


async def test_a_failed_call_still_counts_its_tokens():
    cut = LLMError("truncated", "cut off", Usage(1500, 1024))
    counter = FakeCounter()
    await node(FakeLLM(groq=[cut], cloudflare=[GOOD]), counter).run("jazz")
    assert counter.tokens[("groq", DAY)] == 2524
    assert counter.calls == {("groq", DAY): 1, ("cloudflare", DAY): 1}


async def test_a_lost_token_count_doesnt_fail_the_call():
    class Flaky(FakeCounter):
        async def add_tokens(self, *a):
            raise UsageError("add_provider_tokens failed: ConnectError")

    result = await node(FakeLLM(groq=[GOOD]), Flaky()).run("jazz")
    assert result.status == "ok"


# Through PostgREST ------------------------------------------------------------------


def usage(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabaseUsage(http, "https://x.supabase.co/rest/v1", "service-key"), http


async def test_reserve_calls_the_function_with_counts_only():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=True)

    counter, http = usage(handler)
    async with http:
        ok = await counter.reserve(
            "groq", DAY, ProviderLimit(calls_per_day=1000, tokens_per_day=200000)
        )
    (request,) = seen
    assert ok is True and request.url.path == "/rest/v1/rpc/reserve_provider_call"
    assert json.loads(request.content) == {
        "p_service": "groq",
        "p_day": "2026-10-28",
        "p_call_limit": 1000,
        "p_token_limit": 200000,
    }
    assert request.headers["authorization"] == "Bearer service-key"


async def test_add_tokens_calls_the_function_and_accepts_an_empty_reply():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(204)

    counter, http = usage(handler)
    async with http:
        await counter.add_tokens(
            "groq", DAY, 1800, ProviderLimit(calls_per_day=1000, tokens_per_day=200000)
        )
    assert seen == [
        {"p_service": "groq", "p_day": "2026-10-28", "p_tokens": 1800, "p_token_limit": 200000}
    ]


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(200, json=None)])
async def test_a_bad_reply_is_a_usage_error(response):
    counter, http = usage(lambda request: response)
    async with http:
        with pytest.raises(UsageError):
            await counter.reserve("groq", DAY, ProviderLimit(calls_per_day=1, tokens_per_day=None))


async def test_no_service_key_is_a_usage_error():
    counter = SupabaseUsage(httpx.AsyncClient(), "https://x.supabase.co/rest/v1", None)
    with pytest.raises(UsageError):
        await counter.reserve("groq", DAY, ProviderLimit(calls_per_day=1, tokens_per_day=None))
