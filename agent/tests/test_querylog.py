"""The queries table through PostgREST, against httpx.MockTransport."""

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from app.querylog import QueryLogError, SupabaseQueryLog

pytestmark = pytest.mark.anyio

REST = "https://test-project.supabase.co/rest/v1"
USER = uuid.UUID("0f8fad5b-d9cb-469f-a165-70867728950e")
SINCE = datetime(2026, 10, 28, tzinfo=UTC)


def log_for(handler, key="service-key"):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabaseQueryLog(http, REST, key), http


@pytest.mark.parametrize(("content_range", "count"), [("0-0/7", 7), ("*/0", 0), ("0-0/10", 10)])
async def test_count_asks_postgrest_for_an_exact_count(content_range, count):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[], headers={"content-range": content_range})

    log, http = log_for(handler)
    async with http:
        assert await log.count_today(USER, SINCE) == count
    (request,) = seen
    assert request.url.path == "/rest/v1/queries"
    assert dict(request.url.params) == {
        "select": "id",
        "user_id": f"eq.{USER}",
        "created_at": "gte.2026-10-28T00:00:00+00:00",
        "status": "in.(ok,needs_clarification,ai_resting)",
        "limit": "1",
    }
    assert request.headers["prefer"] == "count=exact"
    assert request.headers["apikey"] == "service-key"
    assert request.headers["authorization"] == "Bearer service-key"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(401),
        httpx.Response(200, json=[]),  # no content-range
        httpx.Response(200, json=[], headers={"content-range": "0-0/*"}),
    ],
    ids=["503", "401", "no count", "unknown count"],
)
async def test_count_failures_raise(response):
    log, http = log_for(lambda request: response)
    async with http:
        with pytest.raises(QueryLogError):
            await log.count_today(USER, SINCE)


async def test_write_posts_the_row_with_minimal_return():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201)

    log, http = log_for(handler)
    row = {"id": str(uuid.uuid4()), "user_id": str(USER), "status": "ok"}
    async with http:
        await log.write(row)
    (request,) = seen
    assert (request.method, request.url.path) == ("POST", "/rest/v1/queries")
    assert json.loads(request.content) == row
    assert request.headers["prefer"] == "return=minimal"


@pytest.mark.parametrize("status", [400, 409, 500])
async def test_write_failures_raise(status):
    log, http = log_for(lambda request: httpx.Response(status))
    async with http:
        with pytest.raises(QueryLogError):
            await log.write({"id": "x"})


async def test_no_service_key_sends_nothing():
    seen = []
    log, http = log_for(lambda request: seen.append(request) or httpx.Response(201), key=None)
    async with http:
        with pytest.raises(QueryLogError, match="SUPABASE_SERVICE_KEY"):
            await log.write({"id": "x"})
        with pytest.raises(QueryLogError):
            await log.count_today(USER, SINCE)
    assert seen == []


QUERY = uuid.UUID("5d0c6a8e-0000-4000-8000-000000000002")


async def test_get_own_reads_only_the_users_own_row():
    # The service key bypasses RLS: this filter is what stops one user refining another's
    # query (decision 0018).
    seen = []
    row = {"id": str(QUERY), "status": "ok", "filters": {}, "embedding_text": None}

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[row])

    log, http = log_for(handler)
    async with http:
        assert await log.get_own(QUERY, USER) == row
    (request,) = seen
    assert request.url.path == "/rest/v1/queries"
    assert dict(request.url.params) == {
        "select": "id,status,filters,embedding_text,vibe,who",
        "id": f"eq.{QUERY}",
        "user_id": f"eq.{USER}",
    }


async def test_get_own_with_no_row_is_none_and_a_failure_is_an_error():
    log, http = log_for(lambda request: httpx.Response(200, json=[]))
    async with http:
        assert await log.get_own(QUERY, USER) is None
    log, http = log_for(lambda request: httpx.Response(503))
    async with http:
        with pytest.raises(QueryLogError):
            await log.get_own(QUERY, USER)


async def test_write_results_posts_the_rows():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201)

    rows = [{"query_id": str(QUERY), "rank": 1, "source": "skiddle", "source_id": "1", "score": 1}]
    log, http = log_for(handler)
    async with http:
        await log.write_results(rows)
    (request,) = seen
    assert request.url.path == "/rest/v1/query_results" and json.loads(request.content) == rows
