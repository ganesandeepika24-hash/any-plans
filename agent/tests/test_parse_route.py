"""POST /parse: auth, responses, the queries row and the daily cap (decision 0013).

Real JWT verification against an in-memory JWKS, the fake LLM, and an in-memory query
log. No network.
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth import get_jwks_client
from app.main import create_app
from app.parse import CAPPED, Parser
from app.querylog import COUNTED_STATUSES, QueryLogError
from app.routes.parse import get_parser
from app.understand.llm import LLMError
from app.understand.node import AI_RESTING, Understander
from app.understand.schema import SearchFilters
from tests.test_auth import KID, FakeJWKSClient, _claims, _jwk, _token
from tests.understand_support import (
    REPO,
    FakeLLM,
    FakeStore,
    RecordingTracer,
    entries,
    reply,
    when,
)

USER = uuid.UUID("0f8fad5b-d9cb-469f-a165-70867728950e")
NOW = datetime.fromisoformat("2026-10-28T20:00:00+00:00")
GOOD = reply(keywords="jazz", categories=["Music"], when=when("tonight"), place="Soho")
CAP = {"per_user_per_day": 10}


class FakeQueryLog:
    def __init__(self):
        self.rows: list[dict] = []
        self.since: list[datetime] = []
        self.fail_count = False
        self.fail_write = False

    async def count_today(self, user_id, since):
        if self.fail_count:
            raise QueryLogError("couldn't count today's queries: ConnectError")
        self.since.append(since)
        return sum(
            1 for r in self.rows if r["user_id"] == str(user_id) and r["status"] in COUNTED_STATUSES
        )

    async def write(self, row):
        if self.fail_write:
            raise QueryLogError("couldn't write the queries row: ConnectError")
        self.rows.append(row)

    def preload(self, n, status="ok", user=USER):
        for _ in range(n):
            self.rows.append({"id": str(uuid.uuid4()), "user_id": str(user), "status": status})


@pytest.fixture(scope="module")
def signing_key():
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def token(signing_key):
    return _token(signing_key, _claims(sub=str(USER)))


class Harness:
    def __init__(self, signing_key, llm=None, config=None, cap=CAP):
        self.llm = llm or FakeLLM(groq=[GOOD], cloudflare=[])
        self.log = FakeQueryLog()
        self.store = FakeStore(config or entries(ai_search_cap=cap))
        understander = Understander(self.store, self.llm, RecordingTracer(), clock=lambda: NOW)
        self.parser = Parser(understander, self.store, self.log, release="test", clock=lambda: NOW)
        app = create_app()
        app.dependency_overrides[get_jwks_client] = lambda: FakeJWKSClient(
            {"keys": [_jwk(signing_key.public_key(), KID)]}
        )
        app.dependency_overrides[get_parser] = lambda: self.parser
        self.client = TestClient(app)

    def post(self, body, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with self.client as client:
            return client.post("/parse", json=body, headers=headers)


@pytest.fixture
def harness(signing_key):
    return Harness(signing_key)


# Auth ------------------------------------------------------------------------------


def test_no_token_is_401_and_nothing_runs(harness):
    response = harness.post({"query": "jazz tonight"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert harness.llm.calls == [] and harness.log.rows == []


def test_no_token_with_a_bad_body_is_still_401(harness):
    assert harness.post({"nope": 1}).status_code == 401


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 1},
        {"aud": "anon"},
        {"iss": "https://other-project.supabase.co/auth/v1"},
        {"sub": "user-123"},
    ],
    ids=["expired", "wrong audience", "wrong issuer", "sub not a uuid"],
)
def test_bad_tokens_are_401(harness, signing_key, claims):
    token = _token(signing_key, _claims(**({"sub": str(USER)} | claims)))
    assert harness.post({"query": "jazz tonight"}, token).status_code == 401
    assert harness.llm.calls == [] and harness.log.rows == []


def test_a_token_signed_by_another_key_is_401(harness):
    from cryptography.hazmat.primitives.asymmetric import ec

    other = ec.generate_private_key(ec.SECP256R1())
    token = _token(other, _claims(sub=str(USER)))
    assert harness.post({"query": "jazz tonight"}, token).status_code == 401


# Responses -------------------------------------------------------------------------


def test_ok_returns_filters_embedding_text_notes_and_ids(harness, token):
    response = harness.post({"query": "  jazz in soho tonight  "}, token)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["reason"] is None and body["message"] is None
    assert body["embedding_text"] == "jazz" and body["notes"] == []
    assert re.fullmatch(r"[0-9a-f]{32}", body["trace_id"])
    uuid.UUID(body["query_id"])
    filters = SearchFilters.model_validate(body["filters"])
    assert filters.to_json() == body["filters"]
    assert body["filters"]["categories"] == ["Music"]
    assert body["filters"]["date_from"] == "2026-10-28T20:00:00+00:00"


def test_notes_come_back_to_the_client(signing_key, token):
    h = Harness(signing_key, llm=FakeLLM(groq=[reply(place="Narnia")]))
    body = h.post({"query": "things in narnia"}, token).json()
    assert body["status"] == "ok" and body["filters"] == {}
    assert [n["code"] for n in body["notes"]] == ["unknown_place"]
    assert "Narnia" in body["notes"][0]["message"]


def test_providers_failing_gives_a_clear_ai_resting(signing_key, token):
    llm = FakeLLM(groq=[LLMError("http_429", "rate limited")], cloudflare=["not json"])
    h = Harness(signing_key, llm=llm)
    response = h.post({"query": "jazz tonight"}, token)
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["reason"], body["message"]) == (
        "ai_resting",
        "providers",
        AI_RESTING,
    )
    assert body["filters"] is None and body["embedding_text"] is None and body["notes"] == []
    assert body["trace_id"]
    (row,) = h.log.rows
    assert row["status"] == "ai_resting" and row["filters"] is None
    assert [a["outcome"] for a in row["attempts"]] == ["http_429", "invalid_json"]


def test_bad_llm_config_rests_as_unavailable(signing_key, token):
    config = entries(ai_search_cap=CAP, llm_understand={"providers": []})
    h = Harness(signing_key, config=config)
    body = h.post({"query": "jazz tonight"}, token).json()
    assert (body["status"], body["reason"]) == ("ai_resting", "unavailable")
    assert h.llm.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 501},
        {},
        {"query": "a", "x": 1},
        {"query": "a", "location": {"lat": 91, "lon": 0}},
        {"query": "a", "location": {"lat": "51.5", "lon": 0}},
        {"query": "a", "location": {"lat": 51.5}},
        {"query": "a", "location": {"lat": 51.5, "lon": 0, "accuracy": 5}},
    ],
    ids=[
        "empty",
        "blank",
        "too long",
        "missing",
        "extra field",
        "latitude out of range",
        "latitude as string",
        "location without lon",
        "location extra field",
    ],
)
def test_bad_bodies_are_422(harness, token, body):
    assert harness.post(body, token).status_code == 422
    assert harness.llm.calls == [] and harness.log.rows == []


# The queries row --------------------------------------------------------------------


def _queries_columns() -> set[str]:
    """queries' columns: 002's create table plus the columns later migrations add."""
    sql = (REPO / "db" / "migrations" / "002_logging.sql").read_text()
    body = re.search(r"create table public\.queries \((.*?)\n\);", sql, re.S).group(1)
    added = re.findall(
        r"add column (\w+)", (REPO / "db" / "migrations" / "013_parse_clarify.sql").read_text()
    )
    return {
        line.split()[0]
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith(("constraint", "--"))
    } | set(added)


def test_the_row_matches_the_queries_columns(harness, token):
    harness.post({"query": "jazz tonight"}, token)
    (row,) = harness.log.rows
    assert set(row) == _queries_columns() - {"created_at"}  # created_at defaults to now()


def test_one_row_per_ok_query_with_everything_needed(harness, token):
    body = harness.post({"query": "  jazz in soho tonight  "}, token).json()
    (row,) = harness.log.rows
    assert row["id"] == body["query_id"]
    assert row["user_id"] == str(USER)
    assert (row["city_id"], row["route"], row["release"]) == ("london", "parse", "test")
    assert row["query_text"] == "jazz in soho tonight"
    assert (row["status"], row["provider"]) == ("ok", "groq")
    assert row["attempts"] == [{"provider": "groq", "model": "openai/gpt-oss-20b", "outcome": "ok"}]
    assert row["filters"] == body["filters"] and row["embedding_text"] == "jazz"
    assert row["notes"] == []
    assert row["trace_id"] == body["trace_id"]
    assert row["config_versions"] == {
        "llm_understand": 1,
        "understand_prompt": 1,
        "ai_search_cap": 1,
    }
    assert isinstance(row["latency_ms"], int) and row["latency_ms"] >= 0


def test_a_failed_log_write_still_answers(harness, token, caplog):
    harness.log.fail_write = True
    response = harness.post({"query": "jazz tonight"}, token)
    assert response.status_code == 200 and response.json()["status"] == "ok"
    assert "not logged" in caplog.text


# The daily cap ----------------------------------------------------------------------


def test_the_cap_counts_since_midnight_utc(harness, token):
    harness.post({"query": "jazz tonight"}, token)
    assert harness.log.since == [datetime(2026, 10, 28, tzinfo=UTC)]


def test_at_the_cap_no_model_is_called(harness, token):
    harness.log.preload(10)
    body = harness.post({"query": "jazz tonight"}, token).json()
    assert (body["status"], body["reason"], body["message"]) == ("ai_resting", "daily_cap", CAPPED)
    assert body["filters"] is None and body["trace_id"] is None
    assert harness.llm.calls == []
    capped = harness.log.rows[-1]
    assert capped["status"] == "capped" and capped["config_versions"] == {"ai_search_cap": 1}


def test_the_tenth_search_runs_and_the_eleventh_is_capped(signing_key, token):
    h = Harness(signing_key, llm=FakeLLM(groq=[GOOD, GOOD]))
    h.log.preload(9)
    assert h.post({"query": "jazz tonight"}, token).json()["status"] == "ok"
    assert h.post({"query": "jazz tonight"}, token).json()["reason"] == "daily_cap"
    assert len(h.llm.calls) == 1


def test_capped_requests_do_not_count(harness, token):
    harness.log.preload(10, status="capped")
    assert harness.post({"query": "jazz tonight"}, token).json()["status"] == "ok"


def test_resting_requests_count(harness, token):
    harness.log.preload(10, status="ai_resting")
    assert harness.post({"query": "jazz tonight"}, token).json()["reason"] == "daily_cap"


def test_other_users_rows_do_not_count(harness, token):
    harness.log.preload(10, user=uuid.uuid4())
    assert harness.post({"query": "jazz tonight"}, token).json()["status"] == "ok"


@pytest.mark.parametrize(("cap", "allowed"), [(0, 0), (2, 2)])
def test_the_cap_comes_from_config(signing_key, token, cap, allowed):
    h = Harness(signing_key, llm=FakeLLM(groq=[GOOD] * 3), cap={"per_user_per_day": cap})
    statuses = [h.post({"query": "jazz tonight"}, token).json()["status"] for _ in range(3)]
    assert statuses == ["ok"] * allowed + ["ai_resting"] * (3 - allowed)


@pytest.mark.parametrize(
    "cap",
    [None, {"per_user_per_day": -1}, {"per_user_per_day": "10"}, {"daily": 10}],
    ids=["missing", "negative", "string", "wrong key"],
)
def test_no_usable_cap_rests_without_calling_a_model(signing_key, token, cap):
    config = entries() if cap is None else entries(ai_search_cap=cap)
    h = Harness(signing_key, config=config)
    body = h.post({"query": "jazz tonight"}, token).json()
    assert (body["status"], body["reason"]) == ("ai_resting", "unavailable")
    assert h.llm.calls == []


def test_an_uncountable_cap_rests_without_calling_a_model(harness, token):
    harness.log.fail_count = True
    body = harness.post({"query": "jazz tonight"}, token).json()
    assert (body["status"], body["reason"]) == ("ai_resting", "unavailable")
    assert harness.llm.calls == []


@pytest.mark.anyio
async def test_parallel_requests_cannot_pass_the_cap_together(signing_key):
    h = Harness(signing_key, llm=FakeLLM(groq=[GOOD, GOOD, GOOD]), cap={"per_user_per_day": 1})
    outcomes = await asyncio.gather(*(h.parser.parse(USER, "jazz tonight") for _ in range(3)))
    reasons = sorted(str(o.response.reason) for o in outcomes)
    assert reasons == ["None", "daily_cap", "daily_cap"]
    assert len(h.llm.calls) == 1


@pytest.mark.anyio
async def test_a_reservation_is_released_after_the_row_is_written_or_fails(signing_key):
    h = Harness(signing_key, llm=FakeLLM(groq=[GOOD, GOOD]), cap={"per_user_per_day": 2})
    h.log.fail_write = True
    first = await h.parser.parse(USER, "jazz tonight")
    await h.parser.record(first)  # write fails: the reservation still goes
    assert not h.parser._pending
    second = await h.parser.parse(USER, "jazz tonight")
    assert second.response.status == "ok"


@pytest.mark.anyio
async def test_a_reservation_is_released_if_understand_raises(signing_key):
    h = Harness(signing_key, cap={"per_user_per_day": 1})

    async def boom(query, location=None):
        raise RuntimeError("bug")

    h.parser._understander.run = boom
    with pytest.raises(RuntimeError):
        await h.parser.parse(USER, "jazz tonight")
    assert not h.parser._pending


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_the_cap_day_is_the_utc_day_even_in_summer_time(signing_key):
    # 00:30 BST on 1 July is still 30 June in UTC, so that's the day that counts.
    h = Harness(signing_key)
    h.parser._clock = lambda: datetime.fromisoformat("2026-07-01T00:30:00+01:00")
    await h.parser.parse(USER, "jazz tonight")
    assert h.log.since == [datetime(2026, 6, 30, tzinfo=UTC)]


# Asking before searching (step 1.5) -------------------------------------------------

NEAR_ME = reply(keywords="something", near_me=True, when=when("tonight"))
WATERLOO = {"lat": 51.5031, "lon": -0.1132}


def test_needs_clarification_answers_with_questions_and_the_filters_that_resolved(
    signing_key, token
):
    h = Harness(signing_key, llm=FakeLLM(groq=[NEAR_ME], cloudflare=[]))
    body = h.post({"query": "something near me tonight"}, token).json()
    assert body["status"] == "needs_clarification" and body["reason"] is None
    assert body["missing"] == ["location"]
    assert body["filters"] == {
        "date_from": "2026-10-28T20:00:00+00:00",
        "date_to": "2026-10-29T05:00:00+00:00",
    }
    (q,) = body["questions"]
    assert q == {
        "reason": "location",
        "question": "Something: which part of London, or should I use your location?",
        "choices": ["Use my location", "Pick an area", "Drop a pin on the map"],
    }
    (row,) = h.log.rows
    assert (row["status"], row["missing"], row["provider"]) == (
        "needs_clarification",
        ["location"],
        "groq",
    )
    assert row["filters"] == body["filters"]


def test_an_ok_reply_has_no_missing_or_questions(harness, token):
    body = harness.post({"query": "jazz in soho tonight"}, token).json()
    assert (body["status"], body["missing"], body["questions"]) == ("ok", [], [])
    assert harness.log.rows[0]["missing"] == []


def test_the_users_location_is_searched_but_never_logged_or_traced(signing_key, token):
    h = Harness(signing_key, llm=FakeLLM(groq=[NEAR_ME], cloudflare=[]))
    tracer = h.parser._understander._tracer
    body = h.post({"query": "something near me tonight", "location": WATERLOO}, token).json()
    assert body["status"] == "ok"
    assert (body["filters"]["lat"], body["filters"]["lon"]) == (51.5031, -0.1132)
    assert body["filters"]["radius_km"] == 1.609344
    (row,) = h.log.rows
    assert "lat" not in row["filters"] and "lon" not in row["filters"]
    assert row["filters"]["radius_km"] == 1.609344
    (trace,) = tracer.sent
    assert "51.5031" not in str(trace.root.attributes) and "-0.1132" not in str(
        trace.root.attributes
    )


def test_a_named_area_is_logged_with_its_point(signing_key, token):
    h = Harness(signing_key)  # GOOD: jazz in Soho
    body = h.post({"query": "jazz in soho tonight", "location": WATERLOO}, token).json()
    assert h.log.rows[0]["filters"]["lat"] == body["filters"]["lat"] != 51.5031


def test_clarify_rows_count_against_the_cap(signing_key, token):
    h = Harness(signing_key, llm=FakeLLM(groq=[GOOD], cloudflare=[]))
    h.log.preload(CAP["per_user_per_day"], status="needs_clarification")
    body = h.post({"query": "jazz tonight"}, token).json()
    assert (body["status"], body["reason"]) == ("ai_resting", "daily_cap")
    assert "needs_clarification" in COUNTED_STATUSES
