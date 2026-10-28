"""POST /search and the Search node (stage 1 step 1, decision 0016). The model, search_items
and the embedder are fakes; the parser, cap, resolver and Search node are the real ones."""

import json

import pytest
from fastapi.testclient import TestClient

from app.auth import get_jwks_client
from app.main import create_app
from app.parse import CAPPED, Parser
from app.routes.search import get_searcher
from app.search import SEARCH_UNAVAILABLE, Searcher, SearchError, SearchNode
from app.understand.llm import LLMError
from app.understand.node import Understander
from tests.search_support import VECTOR, FakeEmbed, FakeSearch, event_row
from tests.test_auth import KID, FakeJWKSClient, _claims, _jwk, _token
from tests.test_parse_route import CAP, GOOD, NOW, USER, FakeQueryLog
from tests.understand_support import FakeLLM, FakeStore, RecordingTracer, entries, reply, when

pytestmark = pytest.mark.anyio
WATERLOO = {"lat": 51.5031, "lon": -0.1132}
NEAR_ME = reply(keywords="jazz", near_me=True, when=when("tonight"))


@pytest.fixture(scope="module")
def signing_key():
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def token(signing_key):
    return _token(signing_key, _claims(sub=str(USER)))


class Harness:
    def __init__(self, signing_key, llm=None, backend=None, embed=None):
        self.llm = llm or FakeLLM(groq=[GOOD], cloudflare=[])
        self.log = FakeQueryLog()
        self.backend = backend or FakeSearch()
        self.embed = embed or FakeEmbed()
        store = FakeStore(entries(ai_search_cap=CAP))
        understander = Understander(store, self.llm, RecordingTracer(), clock=lambda: NOW)
        parser = Parser(understander, store, self.log, release="test", clock=lambda: NOW)
        self.searcher = Searcher(parser, SearchNode(self.backend, self.embed), self.log)
        app = create_app()
        app.dependency_overrides[get_jwks_client] = lambda: FakeJWKSClient(
            {"keys": [_jwk(signing_key.public_key(), KID)]}
        )
        app.dependency_overrides[get_searcher] = lambda: self.searcher
        self.client = TestClient(app)

    def post(self, body, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with self.client as client:
            return client.post("/search", json=body, headers=headers)


# The route --------------------------------------------------------------------------


def test_an_ok_search_returns_ranked_events_and_logs_them(signing_key, token):
    h = Harness(signing_key)
    body = h.post({"query": "jazz in soho tonight"}, token).json()
    assert body["status"] == "ok" and body["filters"]["categories"] == ["Music"]
    assert [(r["rank"], r["title"]) for r in body["results"]] == [(1, "Event 1"), (2, "Event 2")]
    assert body["results"][0]["last_checked_at"] == "2026-10-28T06:00:00Z"
    ((filters, embedding),) = h.backend.calls
    assert filters == body["filters"] and embedding == VECTOR
    assert h.embed.texts == [["jazz"]]
    (row,) = h.log.rows
    assert (row["route"], row["status"], row["id"]) == ("search", "ok", body["query_id"])
    assert h.log.results == [
        {"query_id": body["query_id"], "rank": 1, "source": "ticketmaster",
         "source_id": "src-1", "score": 0.99},
        {"query_id": body["query_id"], "rank": 2, "source": "ticketmaster",
         "source_id": "src-2", "score": 0.98},
    ]  # fmt: skip


def test_search_needs_a_token(signing_key):
    h = Harness(signing_key)
    assert h.post({"query": "jazz tonight"}).status_code == 401
    assert h.backend.calls == [] and h.llm.calls == [] and h.log.rows == []


def test_a_get_is_refused_so_the_location_never_goes_in_a_url(signing_key, token):
    h = Harness(signing_key)
    with h.client as client:
        r = client.get("/search", params={"query": "jazz", **WATERLOO})
    assert r.status_code == 405 and h.backend.calls == []


def test_needs_clarification_never_reaches_search(signing_key, token):
    vague = reply(keywords="fun", unclear=["category"], when=when("this_weekend"))
    h = Harness(signing_key, llm=FakeLLM(groq=[vague], cloudflare=[]))
    body = h.post({"query": "something fun this weekend"}, token).json()
    assert (body["status"], body["missing"], body["results"]) == (
        "needs_clarification",
        ["category"],
        None,
    )
    assert body["questions"][0]["reason"] == "category"
    assert h.backend.calls == [] and h.embed.texts == [] and h.log.results == []
    assert h.log.rows[0]["route"] == "search"


def test_ai_resting_never_reaches_search(signing_key, token):
    slow = LLMError("timeout", "slow")
    h = Harness(signing_key, llm=FakeLLM(groq=[slow], cloudflare=[slow]))
    body = h.post({"query": "jazz tonight"}, token).json()
    assert (body["status"], body["results"]) == ("ai_resting", None)
    assert h.backend.calls == []


def test_search_shares_the_daily_cap_with_parse(signing_key, token):
    h = Harness(signing_key)
    h.log.preload(10)  # ten /parse or /search requests today
    body = h.post({"query": "jazz tonight"}, token).json()
    assert (body["status"], body["reason"], body["message"]) == ("ai_resting", "daily_cap", CAPPED)
    assert h.llm.calls == [] and h.backend.calls == []
    assert h.log.rows[-1]["status"] == "capped" and h.log.rows[-1]["route"] == "search"


def test_a_search_counts_toward_the_cap(signing_key, token):
    h = Harness(signing_key, llm=FakeLLM(groq=[GOOD], cloudflare=[]))
    h.log.preload(9)
    assert h.post({"query": "jazz tonight"}, token).json()["status"] == "ok"
    body = h.post({"query": "jazz tonight"}, token).json()
    assert body["reason"] == "daily_cap"


def test_the_users_location_is_searched_but_never_logged(signing_key, token):
    h = Harness(signing_key, llm=FakeLLM(groq=[NEAR_ME], cloudflare=[]))
    body = h.post({"query": "jazz near me tonight", "location": WATERLOO}, token).json()
    assert body["status"] == "ok"
    ((filters, _),) = h.backend.calls
    assert (filters["lat"], filters["lon"]) == (51.5031, -0.1132)
    assert body["results"][0]["distance_m"] == 350.0  # shown to the user
    assert body["location_source"] is None  # this request didn't say where the point came from
    logged = json.dumps([h.log.rows, h.log.results])
    for secret in ("51.5031", "-0.1132", "distance", "350"):
        assert secret not in logged, secret


def test_a_failed_search_still_answers_and_logs_the_query(signing_key, token):
    h = Harness(signing_key, backend=FakeSearch(fail=True))
    body = h.post({"query": "jazz tonight"}, token).json()
    assert (body["status"], body["results"], body["message"]) == ("ok", None, SEARCH_UNAVAILABLE)
    assert len(h.log.rows) == 1 and h.log.results == []


def test_a_failed_results_write_never_fails_the_request(signing_key, token):
    h = Harness(signing_key)
    h.log.fail_results = True
    assert h.post({"query": "jazz tonight"}, token).status_code == 200
    assert len(h.log.rows) == 1


def test_no_events_logs_no_results(signing_key, token):
    h = Harness(signing_key, backend=FakeSearch(rows=[]))
    body = h.post({"query": "jazz tonight"}, token).json()
    assert body["results"] == [] and h.log.results == []


# The Search node --------------------------------------------------------------------


async def test_a_filter_only_search_isnt_embedded():
    backend, embed = FakeSearch(), FakeEmbed()
    await SearchNode(backend, embed).run({"categories": ["Music"]}, None)
    assert embed.texts == [] and backend.calls == [({"categories": ["Music"]}, None)]


async def test_an_embedding_failure_is_a_search_error():
    with pytest.raises(SearchError):
        await SearchNode(FakeSearch(), FakeEmbed(fail=True)).run({}, "jazz")


async def test_an_event_gone_before_the_lookup_is_dropped_and_ranks_close_up():
    class Gone(FakeSearch):
        async def source_ids(self, event_ids):
            return {i: "src" for i in event_ids[1:]}  # the first was deleted meanwhile

    hits = await SearchNode(Gone([event_row(1), event_row(2), event_row(3)]), FakeEmbed()).run(
        {}, "jazz"
    )
    assert [(h.result.rank, h.result.title) for h in hits] == [(1, "Event 2"), (2, "Event 3")]


async def test_a_row_search_items_shouldnt_return_is_a_search_error():
    with pytest.raises(SearchError):
        await SearchNode(FakeSearch([event_row(1, title=None)]), FakeEmbed()).run({}, "jazz")


# Windows: the next 4 Saturdays (decision 0020) ----------------------------------------

SATURDAYS = [
    ["2026-10-31T05:00:00+00:00", "2026-11-01T05:00:00+00:00"],
    ["2026-11-07T05:00:00+00:00", "2026-11-08T05:00:00+00:00"],
]
TWO_SATURDAYS = {
    "date_from": SATURDAYS[0][0],
    "date_to": SATURDAYS[-1][1],
    "windows": SATURDAYS,
}


class LimitedSearch(FakeSearch):
    """search_items as it ranks: best first, cut to the limit (default 20)."""

    async def search(self, filters, embedding):
        self.calls.append((filters, embedding))
        return self.rows[: filters.get("limit", 20)]


def busy_month() -> list[dict]:
    # 30 weekday events that score higher than the 5 on a Saturday: with search_items'
    # default limit they'd fill all 20 places, and filtering after would leave none.
    weekday = [
        event_row(n, start_at=f"2026-11-0{2 + n % 4}T19:00:00+00:00", score=2 - n / 100)
        for n in range(1, 31)
    ]
    saturday = [event_row(n, start_at="2026-11-07T19:00:00+00:00") for n in range(31, 36)]
    return weekday + saturday


async def test_weekday_events_cant_fill_the_limit_before_the_saturdays():
    backend = LimitedSearch(busy_month())
    hits = await SearchNode(backend, FakeEmbed()).run(TWO_SATURDAYS, None)
    assert [h.result.title for h in hits] == [f"Event {n}" for n in range(31, 36)]
    assert [h.result.rank for h in hits] == [1, 2, 3, 4, 5]
    # search_items got its most rows over the whole span, and never the agent's key.
    ((sent, _),) = backend.calls
    assert sent == {"date_from": SATURDAYS[0][0], "date_to": SATURDAYS[-1][1], "limit": 100}


async def test_the_requested_limit_applies_after_the_filter():
    backend = LimitedSearch(busy_month())
    hits = await SearchNode(backend, FakeEmbed()).run(TWO_SATURDAYS | {"limit": 3}, None)
    assert [h.result.title for h in hits] == ["Event 31", "Event 32", "Event 33"]
    assert backend.calls[0][0]["limit"] == 100


async def test_an_event_running_across_a_saturday_counts_and_one_ending_before_does_not():
    rows = [
        # An exhibition from Thursday to Sunday: on during Saturday.
        event_row(1, start_at="2026-11-05T10:00:00+00:00", end_at="2026-11-08T18:00:00+00:00"),
        # Friday night, ending at 23:00: not on Saturday.
        event_row(2, start_at="2026-11-06T19:00:00+00:00", end_at="2026-11-06T23:00:00+00:00"),
        # No end: the moment it starts, 01:00 on Sunday, still Saturday night.
        event_row(3, start_at="2026-11-08T01:00:00+00:00"),
        # No end, on a Sunday afternoon.
        event_row(4, start_at="2026-11-08T15:00:00+00:00"),
    ]
    hits = await SearchNode(FakeSearch(rows), FakeEmbed()).run(TWO_SATURDAYS, None)
    assert [h.result.title for h in hits] == ["Event 1", "Event 3"]


async def test_without_windows_the_filters_go_through_unchanged():
    backend = LimitedSearch(busy_month())
    hits = await SearchNode(backend, FakeEmbed()).run({"date_from": SATURDAYS[0][0]}, None)
    assert len(hits) == 20 and backend.calls[0][0] == {"date_from": SATURDAYS[0][0]}


def test_search_returns_the_device_source(signing_key, token):
    h = Harness(signing_key, llm=FakeLLM(groq=[NEAR_ME], cloudflare=[]))
    loc = WATERLOO | {"source": "device"}
    body = h.post({"query": "jazz near me tonight", "location": loc}, token).json()
    assert (body["status"], body["location_source"]) == ("ok", "device")
    assert "device" not in json.dumps([h.log.rows, h.log.results])
