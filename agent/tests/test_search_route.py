"""POST /search and the Search node (stage 1 step 1, decision 0016). The model, search_items
and the embedder are fakes; the parser, cap, resolver and Search node are the real ones."""

import json
from datetime import datetime, timedelta

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
from tests.test_parse_route import Harness as ParseHarness
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


def test_results_carry_the_label_and_the_events_point_as_search_items_sent_them(signing_key, token):
    rows = [
        event_row(1),
        event_row(
            2,
            source="skiddle",
            confidence="check_site",
            confidence_label="Check availability on Skiddle",
            lat=None,
            lon=None,
        ),
    ]
    body = Harness(signing_key, backend=FakeSearch(rows)).post({"query": "jazz"}, token).json()
    first, second = body["results"]
    assert (first["confidence"], first["confidence_label"]) == (
        "checked_today",
        "Price checked today",
    )
    assert (first["lat"], first["lon"]) == (51.5133, -0.1318)
    assert (second["confidence"], second["confidence_label"]) == (
        "check_site",
        "Check availability on Skiddle",
    )
    assert (second["lat"], second["lon"]) == (None, None)


def test_rows_without_022s_label_answer_search_unavailable(signing_key, token):
    old = event_row(1)
    del old["confidence"], old["confidence_label"], old["lat"], old["lon"]
    h = Harness(signing_key, backend=FakeSearch([old]))
    body = h.post({"query": "jazz tonight"}, token).json()
    assert (body["status"], body["results"], body["message"]) == ("ok", None, SEARCH_UNAVAILABLE)


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


@pytest.mark.parametrize(
    "fields", [{"title": None}, {"confidence": "verified"}, {"confidence_label": None}]
)
async def test_a_row_search_items_shouldnt_return_is_a_search_error(fields):
    with pytest.raises(SearchError):
        await SearchNode(FakeSearch([event_row(1, **fields)]), FakeEmbed()).run({}, "jazz")


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


async def test_windows_reach_search_items_whole_and_the_limit_is_left_alone():
    # search_items applies windows itself (migration 024): the node passes them through,
    # with no 100-row fetch and no filtering of its own.
    backend = FakeSearch([event_row(1), event_row(2)])
    hits = await SearchNode(backend, FakeEmbed()).run(TWO_SATURDAYS | {"limit": 3}, None)
    ((sent, _),) = backend.calls
    assert sent == TWO_SATURDAYS | {"limit": 3}
    assert [h.result.title for h in hits] == ["Event 1", "Event 2"]  # as search_items sent


async def test_without_windows_the_filters_go_through_unchanged():
    backend = FakeSearch()
    await SearchNode(backend, FakeEmbed()).run({"date_from": SATURDAYS[0][0]}, None)
    assert backend.calls[0][0] == {"date_from": SATURDAYS[0][0]}


EIGHT_SATURDAYS = reply(keywords="markets", when=when("every_weekday", "sat", count=12))


@pytest.mark.parametrize("route", ["/parse", "/search"])
def test_every_window_comes_back_in_full_and_in_order(signing_key, token, route):
    # "The next 12 Saturdays" stops at date_ranges' max_count, 8, with a note. Every one
    # comes back, so the app's URL can carry them all (step 1.4 fix 5).
    harness = Harness if route == "/search" else ParseHarness
    h = harness(signing_key, llm=FakeLLM(groq=[EIGHT_SATURDAYS], cloudflare=[]))
    body = h.post({"query": "markets the next 12 saturdays"}, token).json()
    windows = body["filters"]["windows"]
    assert len(windows) == 8 and "count_capped" in [n["code"] for n in body["notes"]]
    assert windows[0][0] == body["filters"]["date_from"]
    assert windows[-1][1] == body["filters"]["date_to"]
    bounds = [t for w in windows for t in w]
    assert bounds == sorted(bounds)
    starts = [datetime.fromisoformat(a) for a, _ in windows]
    assert all(b - a == timedelta(days=7) for a, b in zip(starts, starts[1:], strict=False))
    assert all(datetime.fromisoformat(a).weekday() == 5 for a, _ in windows[1:])
    # The queries row stores them all, and search_items got them all.
    (row,) = h.log.rows
    assert row["filters"]["windows"] == windows
    if route == "/search":
        assert h.backend.calls[0][0]["windows"] == windows


def test_a_month_of_weekends_comes_back_as_five_windows(signing_key, token):
    weekends = reply(keywords="markets", when=when("every_weekend", month=1))
    h = Harness(signing_key, llm=FakeLLM(groq=[weekends], cloudflare=[]))
    body = h.post({"query": "markets on weekends in january"}, token).json()
    assert len(body["filters"]["windows"]) == 5  # Fridays 1, 8, 15, 22 and 29 Jan 2027


def test_search_returns_the_device_source(signing_key, token):
    h = Harness(signing_key, llm=FakeLLM(groq=[NEAR_ME], cloudflare=[]))
    loc = WATERLOO | {"source": "device"}
    body = h.post({"query": "jazz near me tonight", "location": loc}, token).json()
    assert (body["status"], body["location_source"]) == ("ok", "device")
    assert "device" not in json.dumps([h.log.rows, h.log.results])
