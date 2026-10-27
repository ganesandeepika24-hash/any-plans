"""Langfuse over plain HTTP: the OTLP JSON payload, auth, and failures that don't matter."""

import base64
import json

import httpx
import pytest

from app.config import Settings
from app.main import build_understander
from app.understand.tracing import LangfuseTracer, NullTracer

pytestmark = pytest.mark.anyio


def sample_trace(tracer):
    trace = tracer.start("understand", {"query": "jazz tonight"})
    trace.generation(
        name="understand.groq",
        model="openai/gpt-oss-20b",
        start_ns=trace.root.start_ns,
        input=[{"role": "user", "content": "jazz tonight"}],
        output='{"keywords": "jazz"}',
        usage={"input": 412, "output": 96},
        metadata={"provider": "groq", "outcome": "ok", "llm_understand_version": 1},
    )
    trace.finish(output={"status": "ok"}, metadata={"llm_understand_version": 1})
    return trace


def values(span) -> dict:
    out = {}
    for a in span["attributes"]:
        ((kind, value),) = a["value"].items()
        out[a["key"]] = int(value) if kind == "intValue" else value
    return out


async def test_sends_otlp_json_with_basic_auth_and_the_ingestion_version():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tracer = LangfuseTracer(http, "https://cloud.langfuse.com/", "pk-lf-1", "sk-lf-2", "v9")
        trace = sample_trace(tracer)
        await tracer.send(trace)

    (request,) = seen
    assert str(request.url) == "https://cloud.langfuse.com/api/public/otel/v1/traces"
    assert (
        request.headers["authorization"] == "Basic " + base64.b64encode(b"pk-lf-1:sk-lf-2").decode()
    )
    assert request.headers["x-langfuse-ingestion-version"] == "4"

    body = json.loads(request.content)
    (resource,) = body["resourceSpans"]
    (scope,) = resource["scopeSpans"]
    root, generation = scope["spans"]
    assert len(root["traceId"]) == 32 and len(root["spanId"]) == 16
    assert generation["traceId"] == root["traceId"]
    assert generation["parentSpanId"] == root["spanId"] and "parentSpanId" not in root
    assert int(generation["endTimeUnixNano"]) >= int(generation["startTimeUnixNano"])

    r, g = values(root), values(generation)
    assert r["langfuse.trace.name"] == "understand"
    assert r["langfuse.release"] == "v9"
    assert json.loads(r["langfuse.observation.input"]) == {"query": "jazz tonight"}
    assert r["langfuse.trace.metadata.llm_understand_version"] == 1
    assert g["langfuse.observation.type"] == "generation"
    assert g["langfuse.observation.model.name"] == "openai/gpt-oss-20b"
    assert json.loads(g["langfuse.observation.usage_details"]) == {"input": 412, "output": 96}
    assert g["langfuse.observation.metadata.outcome"] == "ok"
    assert generation["status"] == {"code": 1}


async def test_an_error_span_carries_status_and_message():
    tracer = NullTracer()
    trace = tracer.start("understand", {"query": "q"})
    trace.finish(output=None, metadata={}, error="AI search is resting")
    root = trace.to_otlp()["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert root["status"] == {"code": 2, "message": "AI search is resting"}
    assert values(root)["langfuse.observation.level"] == "ERROR"


@pytest.mark.parametrize("failure", ["status", "network"])
async def test_a_failed_send_is_logged_not_raised(failure, caplog):
    def handler(request):
        if failure == "network":
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tracer = LangfuseTracer(http, "https://cloud.langfuse.com", "pk", "sk", "dev")
        await tracer.send(sample_trace(tracer))
    assert "not sent" in caplog.text


async def test_the_payload_never_contains_keys():
    async with httpx.AsyncClient() as http:
        tracer = LangfuseTracer(http, "https://cloud.langfuse.com", "pk-lf-1", "sk-lf-2", "dev")
        payload = json.dumps(sample_trace(tracer).to_otlp())
    assert "sk-lf-2" not in payload and "pk-lf-1" not in payload


async def test_tracing_is_off_without_langfuse_keys():
    settings = Settings(supabase_url="https://x.supabase.co")
    async with httpx.AsyncClient() as http:
        assert isinstance(build_understander(settings, http)._tracer, NullTracer)
        with_keys = settings.model_copy(
            update={"langfuse_public_key": "pk", "langfuse_secret_key": "sk"}
        )
        assert isinstance(build_understander(with_keys, http)._tracer, LangfuseTracer)
