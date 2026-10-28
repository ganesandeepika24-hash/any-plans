"""Config and city context through PostgREST, against httpx.MockTransport."""

import json
from datetime import date

import httpx
import pytest

from app.understand.store import CONFIG_TTL_S, CONTEXT_TTL_S, StoreError, SupabaseStore
from tests.understand_support import migration_config

pytestmark = pytest.mark.anyio

REST = "https://test-project.supabase.co/rest/v1"
KEYS = ("llm_understand", "understand_prompt")
CONFIG_ROWS = [
    {"key": k, "version": 1, "value_json": v} for k, v in migration_config().items() if k in KEYS
]
CONTEXT = {
    "city": {"id": "london", "name": "London", "timezone": "Europe/London"},
    "areas": [
        {
            "id": "london-soho",
            "name": "Soho",
            "kind": "neighbourhood",
            "aliases": [],
            "lat": 51.5136,
            "lon": -0.1365,
            "default_radius_km": 1,
        }
    ],
    "calendar": [
        {
            "id": "london-2027-eid-al-fitr",
            "name": "Eid al-Fitr",
            "aliases": ["Eid"],
            "starts_on": "2027-03-09",
            "ends_on": "2027-03-10",
            "estimate": True,
        }
    ],
}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Server:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.down = False
        self.config = CONFIG_ROWS
        self.context = CONTEXT

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            return httpx.Response(503)
        if request.url.path.endswith("/config"):
            return httpx.Response(200, json=self.config)
        # PostgREST sends a scalar function's null as the JSON literal null.
        return httpx.Response(200, content=json.dumps(self.context).encode())


def store(server, clock=None, key="service-key"):
    http = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return SupabaseStore(http, REST, key, clock=clock or Clock()), http


async def test_config_reads_active_versions_with_the_service_key():
    server = Server()
    s, http = store(server)
    async with http:
        got = await s.config(KEYS)
    (request,) = server.requests
    assert request.url.path == "/rest/v1/config"
    assert dict(request.url.params) == {
        "select": "key,version,value_json",
        "is_active": "is.true",
        "key": "in.(llm_understand,understand_prompt)",
    }
    assert request.headers["apikey"] == "service-key"
    assert request.headers["authorization"] == "Bearer service-key"
    assert {k: e.version for k, e in got.items()} == {"llm_understand": 1, "understand_prompt": 1}


async def test_config_is_reread_after_a_minute():
    server, clock = Server(), Clock()
    s, http = store(server, clock)
    async with http:
        await s.config(KEYS)
        clock.now = CONFIG_TTL_S - 1
        await s.config(KEYS)
        assert len(server.requests) == 1
        server.config = [dict(r, version=2) for r in CONFIG_ROWS]
        clock.now = CONFIG_TTL_S + 1
        got = await s.config(KEYS)
    assert len(server.requests) == 2 and got["llm_understand"].version == 2


async def test_a_failed_refresh_keeps_the_last_good_config():
    server, clock = Server(), Clock()
    s, http = store(server, clock)
    async with http:
        first = await s.config(KEYS)
        server.down = True
        clock.now = CONFIG_TTL_S + 1
        assert await s.config(KEYS) == first


async def test_no_config_and_no_cache_is_an_error():
    server = Server()
    server.down = True
    s, http = store(server)
    async with http:
        with pytest.raises(StoreError):
            await s.config(KEYS)


async def test_a_missing_active_key_is_an_error():
    server = Server()
    server.config = CONFIG_ROWS[:1]
    s, http = store(server)
    async with http:
        with pytest.raises(StoreError, match="understand_prompt"):
            await s.config(KEYS)


async def test_no_service_key_sends_nothing():
    server = Server()
    s, http = store(server, key=None)
    async with http:
        with pytest.raises(StoreError, match="SUPABASE_SERVICE_KEY"):
            await s.config(KEYS)
    assert server.requests == []


async def test_city_context_comes_from_understand_context_and_is_kept_an_hour():
    server, clock = Server(), Clock()
    s, http = store(server, clock)
    async with http:
        city = await s.city("london")
        clock.now = CONTEXT_TTL_S - 1
        await s.city("london")
    (request,) = server.requests
    assert request.url.path == "/rest/v1/rpc/understand_context"
    assert json.loads(request.content) == {"city_id": "london"}
    assert (city.name, str(city.tz), city.find_area("soho").lat) == (
        "London",
        "Europe/London",
        51.5136,
    )


async def test_city_context_carries_the_calendar():
    s, http = store(Server())
    async with http:
        city = await s.city("london")
    (eid,) = city.calendar
    assert (eid.name, eid.aliases, eid.first, eid.last, eid.estimate) == (
        "Eid al-Fitr",
        ("Eid",),
        date(2027, 3, 9),
        date(2027, 3, 10),
        True,
    )
    assert city.find_date("eid", date(2027, 3, 10)) == eid
    assert city.find_date("eid", date(2027, 3, 11)) is None


async def test_a_context_without_a_calendar_has_none():
    # understand_context from 011, before 019 is applied.
    server = Server()
    server.context = {k: v for k, v in CONTEXT.items() if k != "calendar"}
    s, http = store(server)
    async with http:
        assert (await s.city("london")).calendar == ()


async def test_an_unknown_city_is_an_error():
    server = Server()
    server.context = None
    s, http = store(server)
    async with http:
        with pytest.raises(StoreError, match="no city"):
            await s.city("atlantis")
