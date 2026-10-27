"""The Groq and Cloudflare adapters, against httpx.MockTransport and synthetic responses."""

import json

import httpx
import pytest

from app.understand.llm import (
    CLOUDFLARE_URL,
    GROQ_URL,
    LLMConfig,
    LLMError,
    OpenAICompatibleClient,
)
from app.understand.node import parse_reply
from app.understand.schema import UnderstoodQuery, strict_json_schema
from tests.understand_support import FIXTURES, migration_config

pytestmark = pytest.mark.anyio

PROVIDERS = {
    p.provider: p for p in LLMConfig.model_validate(migration_config()["llm_understand"]).providers
}
SCHEMA = strict_json_schema(UnderstoodQuery)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / "llm" / name).read_text())


def client(handler, groq="gsk-test", cf_token="cf-test", cf_account="acct123"):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleClient(http, groq, cf_token, cf_account), http


def responding(status=200, body=None, headers=None, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body, headers=headers)

    return handler


async def test_groq_request_uses_config_strict_schema_and_bearer_key():
    seen = []
    llm, http = client(responding(body=fixture("groq_ok.json"), seen=seen))
    async with http:
        completion = await llm.complete(PROVIDERS["groq"], "SYSTEM", "jazz tonight", SCHEMA)
    (request,) = seen
    body = json.loads(request.content)
    assert str(request.url) == GROQ_URL
    assert request.headers["authorization"] == "Bearer gsk-test"
    assert body["model"] == "openai/gpt-oss-20b"
    assert body["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "jazz tonight"},
    ]
    assert (body["temperature"], body["max_completion_tokens"], body["reasoning_effort"]) == (
        0,
        1024,
        "low",
    )
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "understood_query", "strict": True, "schema": SCHEMA},
    }
    assert (completion.usage.input_tokens, completion.usage.output_tokens) == (412, 96)
    assert parse_reply(completion.text).place == "Soho"


async def test_cloudflare_request_goes_to_the_account_endpoint_without_response_format():
    seen = []
    llm, http = client(responding(body=fixture("cloudflare_ok_fenced.json"), seen=seen))
    async with http:
        completion = await llm.complete(PROVIDERS["cloudflare"], "SYSTEM", "jazz tonight", None)
    (request,) = seen
    body = json.loads(request.content)
    assert str(request.url) == CLOUDFLARE_URL.format(account_id="acct123")
    assert request.headers["authorization"] == "Bearer cf-test"
    assert body["model"] == "@cf/openai/gpt-oss-20b"
    assert body["max_tokens"] == 1024
    assert "response_format" not in body and "reasoning_effort" not in body
    # One surrounding code fence is unwrapped; the JSON inside must still validate.
    assert parse_reply(completion.text).keywords == "jazz"


@pytest.mark.parametrize(
    ("status", "headers", "outcome"),
    [
        (429, {"retry-after": "7"}, "http_429"),
        (500, None, "http_5xx"),
        (503, None, "http_5xx"),
        (401, None, "http_4xx"),
        (400, None, "http_4xx"),
    ],
)
async def test_http_errors_become_outcomes(status, headers, outcome):
    llm, http = client(responding(status=status, body={"error": {"message": "x"}}, headers=headers))
    async with http:
        with pytest.raises(LLMError) as err:
            await llm.complete(PROVIDERS["groq"], "S", "q", SCHEMA)
    assert err.value.outcome == outcome


async def test_a_timeout_is_an_outcome():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    llm, http = client(handler)
    async with http:
        with pytest.raises(LLMError) as err:
            await llm.complete(PROVIDERS["groq"], "S", "q", SCHEMA)
    assert err.value.outcome == "timeout"


async def test_a_network_failure_is_an_outcome():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    llm, http = client(handler)
    async with http:
        with pytest.raises(LLMError) as err:
            await llm.complete(PROVIDERS["cloudflare"], "S", "q", None)
    assert err.value.outcome == "network"


@pytest.mark.parametrize(
    ("body", "outcome"),
    [
        (fixture("groq_truncated.json"), "truncated"),
        ({"choices": [{"message": {"content": "   "}, "finish_reason": "stop"}]}, "empty"),
        ({"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}, "empty"),
        ({"choices": []}, "invalid_json"),
        ({"detail": "not a completion"}, "invalid_json"),
    ],
    ids=["cut off by reasoning", "blank", "null content", "no choices", "not a completion"],
)
async def test_unusable_completions_are_outcomes(body, outcome):
    llm, http = client(responding(body=body))
    async with http:
        with pytest.raises(LLMError) as err:
            await llm.complete(PROVIDERS["groq"], "S", "q", SCHEMA)
    assert err.value.outcome == outcome


async def test_truncation_keeps_token_counts_for_the_trace():
    llm, http = client(responding(body=fixture("groq_truncated.json")))
    async with http:
        with pytest.raises(LLMError) as err:
            await llm.complete(PROVIDERS["groq"], "S", "q", SCHEMA)
    assert err.value.usage.output_tokens == 1024


@pytest.mark.parametrize(
    ("provider", "kwargs"),
    [
        ("groq", {"groq": None}),
        ("cloudflare", {"cf_token": None}),
        ("cloudflare", {"cf_account": None}),
    ],
)
async def test_a_missing_key_is_an_outcome_and_sends_nothing(provider, kwargs):
    seen = []
    llm, http = client(responding(body={}, seen=seen), **kwargs)
    async with http:
        with pytest.raises(LLMError) as err:
            await llm.complete(PROVIDERS[provider], "S", "q", None)
    assert err.value.outcome == "no_key" and seen == []


@pytest.mark.parametrize(
    ("name", "outcome"),
    [("groq_prose.json", "invalid_json"), ("cloudflare_extra_key.json", "schema")],
)
def test_replies_that_are_not_a_valid_understood_query_are_refused(name, outcome):
    text = fixture(name)["choices"][0]["message"]["content"]
    with pytest.raises(LLMError) as err:
        parse_reply(text)
    assert err.value.outcome == outcome


@pytest.mark.parametrize(
    "text",
    [
        'Here you go: {"keywords": null}',
        '```json\n{"a": 1}\n``` and more',
        "```python\n{}\n```",
    ],
)
def test_only_a_bare_object_or_one_json_fence_is_accepted(text):
    with pytest.raises(LLMError):
        parse_reply(text)


def test_error_messages_never_contain_keys():
    llm = OpenAICompatibleClient(httpx.AsyncClient(), None, None, None)
    with pytest.raises(LLMError) as err:
        llm._endpoint(PROVIDERS["groq"])
    assert "gsk" not in str(err.value)


@pytest.mark.parametrize(
    "template",
    [
        "```json\n{reply}\n``` Hope this helps!",
        "Here you go:\n```json\n{reply}\n```",
        "{reply} Enjoy!",
    ],
    ids=["text after the fence", "text before the fence", "text after the object"],
)
def test_a_valid_reply_with_extra_text_around_it_is_refused(template):
    from tests.understand_support import reply

    with pytest.raises(LLMError) as err:
        parse_reply(template.replace("{reply}", reply(keywords="jazz")))
    assert err.value.outcome == "invalid_json"


def test_a_valid_reply_in_one_fence_is_accepted():
    from tests.understand_support import reply

    assert parse_reply("```json\n" + reply(keywords="jazz") + "\n```").keywords == "jazz"
    assert parse_reply("```\n" + reply(keywords="jazz") + "\n```").keywords == "jazz"
