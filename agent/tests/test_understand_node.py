"""The Understand node: provider order from config, strict validation, fallback, tracing."""

import json
from datetime import datetime

import pytest

from app.graph import build_graph
from app.search import SearchNode
from app.understand.llm import LLMError
from app.understand.node import AI_RESTING, Understander
from app.understand.store import StoreError
from tests.search_support import VECTOR, FakeEmbed, FakeSearch
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

NOW = datetime.fromisoformat("2026-10-28T20:00:00+00:00")
GOOD = reply(keywords="jazz", categories=["Music"], when=when("tonight"), place="Soho")


def understander(llm, store=None, tracer=None):
    return Understander(store or FakeStore(), llm, tracer or RecordingTracer(), clock=lambda: NOW)


async def test_groq_answers_and_cloudflare_is_never_called():
    llm = FakeLLM(groq=[GOOD], cloudflare=[])
    result = await understander(llm).run("jazz in soho tonight")
    assert result.status == "ok" and result.provider == "groq"
    assert [c["provider"] for c in llm.calls] == ["groq"]
    assert result.filters.categories == ["Music"] and result.filters.radius_km == 1.0
    assert result.filters.date_from.isoformat() == "2026-10-28T20:00:00+00:00"
    assert result.query_text == "jazz"
    assert [(a.provider, a.outcome) for a in result.attempts] == [("groq", "ok")]


@pytest.mark.parametrize(
    "failure",
    [
        LLMError("timeout", "no reply within 8 s"),
        LLMError("http_429", "rate limited"),
        LLMError("http_5xx", "HTTP 503"),
        LLMError("http_4xx", "HTTP 401"),
        LLMError("network", "ConnectError"),
        LLMError("no_key", "GROQ_API_KEY is not set"),
        LLMError("truncated", "cut off"),
        LLMError("empty", "no reply text"),
        "Sure! Here are some events.",
        reply(when=when("weekday")),
        json.dumps(json.loads(GOOD) | {"confidence": 0.9}),
        GOOD.replace('"Music"', '"Jazz"'),
    ],
    ids=[
        "timeout",
        "429",
        "5xx",
        "4xx",
        "network",
        "no key",
        "truncated",
        "empty",
        "prose",
        "weekday missing",
        "extra key",
        "unknown category",
    ],
)
async def test_any_failure_falls_back_to_the_next_provider(failure):
    llm = FakeLLM(groq=[failure], cloudflare=[GOOD])
    result = await understander(llm).run("jazz in soho tonight")
    assert result.status == "ok" and result.provider == "cloudflare"
    assert [a.provider for a in result.attempts] == ["groq", "cloudflare"]
    assert result.attempts[0].outcome != "ok" and result.attempts[1].outcome == "ok"


async def test_nothing_is_guessed_when_every_provider_fails():
    llm = FakeLLM(groq=[LLMError("http_429", "rate limited")], cloudflare=["not json"])
    result = await understander(llm).run("jazz tonight")
    assert result.status == "ai_resting" and result.message == AI_RESTING
    assert result.filters is None and result.understood is None
    assert [a.outcome for a in result.attempts] == ["http_429", "invalid_json"]


async def test_the_unstructured_provider_gets_the_schema_in_its_prompt():
    llm = FakeLLM(groq=[LLMError("timeout", "slow")], cloudflare=[GOOD])
    await understander(llm).run("jazz tonight")
    groq, cloudflare = llm.calls
    system = migration_config()["understand_prompt"]["system"]
    assert groq["system"] == system and groq["schema"] is not None
    assert cloudflare["system"].startswith(system) and cloudflare["schema"] is None
    assert '"additionalProperties":false' in cloudflare["system"]


async def test_provider_order_and_models_come_from_config():
    config = migration_config()["llm_understand"]
    swapped = {"providers": list(reversed(config["providers"]))}
    store = FakeStore(entries(llm_understand=swapped))
    llm = FakeLLM(groq=[GOOD], cloudflare=[GOOD])
    result = await understander(llm, store).run("jazz tonight")
    assert result.provider == "cloudflare"
    assert [c["model"] for c in llm.calls] == ["@cf/openai/gpt-oss-20b"]


async def test_the_prompt_comes_from_config():
    store = FakeStore(entries(understand_prompt={"system": "PROMPT v2"}))
    llm = FakeLLM(groq=[GOOD])
    await understander(llm, store).run("jazz tonight")
    assert llm.calls[0]["system"] == "PROMPT v2"


@pytest.mark.parametrize(
    "store",
    [
        FakeStore(entries(llm_understand={"providers": []})),
        FakeStore(entries(llm_understand={"providers": [{"provider": "openai", "model": "x"}]})),
        FakeStore(entries(understand_prompt={"system": ""})),
        FakeStore({k: v for k, v in entries().items() if k != "understand_prompt"}),
        FakeStore({k: v for k, v in entries().items() if k != "date_ranges"}),
        FakeStore(entries(date_ranges={"seasons": {}})),
    ],
    ids=[
        "no providers",
        "unknown provider",
        "empty prompt",
        "prompt missing",
        "date ranges missing",
        "date ranges invalid",
    ],
)
async def test_bad_config_rests_without_calling_any_model(store):
    llm = FakeLLM(groq=[GOOD], cloudflare=[GOOD])
    result = await understander(llm, store).run("jazz tonight")
    assert result.status == "ai_resting" and llm.calls == []
    assert result.message.startswith(AI_RESTING)


async def test_an_unreachable_store_rests():
    store = FakeStore()
    store.fail = StoreError("config unavailable")
    result = await understander(FakeLLM(groq=[GOOD]), store).run("jazz tonight")
    assert result.status == "ai_resting"


@pytest.mark.parametrize("query", ["", "   ", "x" * 501])
async def test_queries_must_be_1_to_500_characters(query):
    with pytest.raises(ValueError):
        await understander(FakeLLM()).run(query)


async def test_a_recurring_day_asks_with_its_choices():
    llm = FakeLLM(groq=[reply(keywords="Hamilton", when=when("every_weekday", "sat"))])
    result = await understander(llm).run("Hamilton on a Saturday")
    assert (result.status, result.missing) == ("needs_clarification", ("date",))
    (q,) = result.questions
    assert q.choices == ("Next 4 Saturdays", "This month", "Pick a month")
    assert result.config_versions["date_ranges"] == 1


async def test_unresolved_parts_come_back_as_notes():
    llm = FakeLLM(groq=[reply(place="Narnia", when=when("date", month=2, day=30))])
    result = await understander(llm).run("something in narnia on 30 feb")
    assert result.status == "ok"
    assert [n.code for n in result.notes] == ["no_such_date", "unknown_place"]
    # No date, and the default area in place of Narnia (decision 0021).
    assert result.filters.to_json() == {"lat": 51.507333, "lon": -0.127654, "radius_km": 10.0}


# Tracing ----------------------------------------------------------------------------


def attrs(span) -> dict:
    return span.attributes


async def test_every_attempt_is_traced_with_the_config_versions():
    tracer = RecordingTracer()
    store = FakeStore(entries({"llm_understand": 3, "understand_prompt": 2}))
    llm = FakeLLM(groq=[LLMError("http_429", "rate limited")], cloudflare=[GOOD])
    await understander(llm, store, tracer).run("jazz in soho tonight")

    (trace,) = tracer.sent
    groq, cloudflare = trace.generations
    assert attrs(groq)["langfuse.observation.model.name"] == "openai/gpt-oss-20b"
    assert attrs(groq)["langfuse.observation.metadata.outcome"] == "http_429"
    assert attrs(groq)["langfuse.observation.level"] == "ERROR"
    assert attrs(cloudflare)["langfuse.observation.metadata.outcome"] == "ok"
    assert attrs(cloudflare)["langfuse.observation.usage_details"] == {"input": 120, "output": 40}
    for span in (trace.root, groq, cloudflare):
        prefix = "trace" if span is trace.root else "observation"
        assert attrs(span)[f"langfuse.{prefix}.metadata.llm_understand_version"] == 3
        assert attrs(span)[f"langfuse.{prefix}.metadata.understand_prompt_version"] == 2
    root = attrs(trace.root)
    assert root["langfuse.observation.input"] == {"query": "jazz in soho tonight"}
    assert root["langfuse.observation.output"]["provider"] == "cloudflare"
    assert root["langfuse.release"] == "test"


async def test_a_resting_trace_is_marked_as_an_error():
    tracer = RecordingTracer()
    llm = FakeLLM(groq=["nope"], cloudflare=["nope"])
    await understander(llm, tracer=tracer).run("jazz tonight")
    (trace,) = tracer.sent
    assert trace.root.error and attrs(trace.root)["langfuse.observation.level"] == "ERROR"
    assert [attrs(g)["langfuse.observation.metadata.outcome"] for g in trace.generations] == [
        "invalid_json",
        "invalid_json",
    ]


async def test_failed_replies_are_traced_with_their_text():
    tracer = RecordingTracer()
    llm = FakeLLM(groq=["Sure! Here you go."], cloudflare=[GOOD])
    await understander(llm, tracer=tracer).run("jazz tonight")
    groq = tracer.sent[0].generations[0]
    assert attrs(groq)["langfuse.observation.output"] == "Sure! Here you go."


# The graph --------------------------------------------------------------------------


async def test_the_graph_searches_an_ok_parse_with_its_filters():
    backend, embed = FakeSearch(), FakeEmbed()
    graph = build_graph(understander(FakeLLM(groq=[GOOD])), SearchNode(backend, embed))
    state = await graph.ainvoke({"query": "jazz in soho tonight"})
    assert state["status"] == "ok"
    assert state["filters"]["categories"] == ["Music"]
    assert state["filters"]["date_from"] == "2026-10-28T20:00:00+00:00"
    assert state["query_text"] == "jazz" and state["notes"] == []
    assert backend.calls == [(state["filters"], VECTOR)] and embed.texts == [["jazz"]]
    assert [r["rank"] for r in state["results"]] == [1, 2]


async def test_the_graph_reports_ai_resting_without_searching():
    backend = FakeSearch()
    graph = build_graph(
        understander(FakeLLM(groq=["x"], cloudflare=["x"])), SearchNode(backend, FakeEmbed())
    )
    state = await graph.ainvoke({"query": "jazz tonight"})
    assert (state["status"], state["filters"], state["message"]) == ("ai_resting", None, AI_RESTING)
    assert backend.calls == [] and "results" not in state


async def test_needs_clarification_never_reaches_search():
    backend = FakeSearch()
    vague = reply(keywords="fun", unclear=["category"], when=when("this_weekend"))
    graph = build_graph(understander(FakeLLM(groq=[vague])), SearchNode(backend, FakeEmbed()))
    state = await graph.ainvoke({"query": "something fun this weekend"})
    assert (state["status"], state["missing"]) == ("needs_clarification", ["category"])
    assert backend.calls == [] and "results" not in state
