"""POST /refine (stage 1 step 2, decision 0018). The model, search_items and the embedder
are fakes; the parser, cap, Refine step and Search node are the real ones."""

import json
import uuid

import pytest

from app.parse import CAPPED
from app.understand.schema import RefineReply
from tests.search_support import FakeSearch
from tests.test_auth import _claims, _token
from tests.test_parse_route import USER
from tests.test_search_route import Harness
from tests.understand_support import FakeLLM, reply

OTHER = uuid.UUID("9b2f4c1e-0000-4000-8000-000000000001")
PRIOR_ID = uuid.UUID("5d0c6a8e-0000-4000-8000-000000000002")
WATERLOO = {"lat": 51.5031, "lon": -0.1132, "source": "device"}
WEEKEND = {"date_from": "2026-10-30T17:00:00+00:00", "date_to": "2026-11-02T05:00:00+00:00"}


def change(price_change=None, distance_change=None, **fields) -> str:
    text = json.loads(reply(**fields)) | {
        "price_change": price_change,
        "distance_change": distance_change,
    }
    return RefineReply.model_validate(text).model_dump_json()


CHEAPER = change(price_change="cheaper")


@pytest.fixture(scope="module")
def signing_key():
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def token(signing_key):
    return _token(signing_key, _claims(sub=str(USER)))


class RefineHarness(Harness):
    def __init__(self, signing_key, llm=None, **kw):
        super().__init__(signing_key, llm=llm or FakeLLM(groq=[CHEAPER], cloudflare=[]), **kw)

    def prior(self, user=USER, status="ok", filters=None, **fields):
        self.log.rows.append(
            {
                "id": str(PRIOR_ID),
                "user_id": str(user),
                "route": "search",
                "status": status,
                "filters": filters
                if filters is not None or status != "ok"
                else WEEKEND | {"categories": ["Music"], "price_max": 40},
                "embedding_text": "live music",
                "vibe": None,
                "who": None,
            }
            | fields
        )

    def refine(self, body, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with self.client as client:
            return client.post("/refine", json=body, headers=headers)


def body(message="make it cheaper", **extra):
    return {"query_id": str(PRIOR_ID), "message": message} | extra


def test_a_refine_changes_the_earlier_search_and_searches_again(signing_key, token):
    h = RefineHarness(signing_key)
    h.prior()
    r = h.refine(body(), token)
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok" and b["filters"] == WEEKEND | {
        "categories": ["Music"],
        "price_max": 20,
    }
    assert [x["rank"] for x in b["results"]] == [1, 2]
    ((filters, _),) = h.backend.calls
    assert filters == b["filters"] and h.embed.texts == [["live music"]]
    row = h.log.rows[-1]
    assert (row["route"], row["refines"], row["status"]) == ("refine", str(PRIOR_ID), "ok")
    assert row["filters"]["price_max"] == 20  # stored, so a later refine can read it
    assert {r["query_id"] for r in h.log.results} == {b["query_id"]}
    assert h.llm.calls[0]["user"] == "make it cheaper"


def test_a_refine_of_a_refine_chains(signing_key, token):
    h = RefineHarness(signing_key, llm=FakeLLM(groq=[CHEAPER, CHEAPER], cloudflare=[]))
    h.prior()
    first = h.refine(body(), token).json()
    second = h.refine({"query_id": first["query_id"], "message": "even cheaper"}, token).json()
    assert second["filters"]["price_max"] == 10
    assert h.log.rows[-1]["refines"] == first["query_id"]


@pytest.mark.parametrize("owner", [OTHER, None], ids=["someone else's", "no such query"])
def test_only_the_users_own_query_can_be_refined(signing_key, token, owner):
    h = RefineHarness(signing_key)
    if owner:
        h.prior(user=owner)
    r = h.refine(body(), token)
    assert r.status_code == 404 and r.json()["detail"] == "No such earlier search"
    # Nothing spent and nothing logged: not a model call, not a row, not a cap use.
    assert h.llm.calls == [] and h.backend.calls == [] and len(h.log.rows) == (1 if owner else 0)


@pytest.mark.parametrize(
    ("status", "filters"),
    [
        ("needs_clarification", {"price_max": 40}),  # it keeps what did resolve (013)
        ("ai_resting", None),
        ("capped", None),
    ],
)
def test_an_unfinished_query_cant_be_refined(signing_key, token, status, filters):
    h = RefineHarness(signing_key)
    h.prior(status=status, filters=filters)
    assert h.refine(body(), token).status_code == 409
    assert h.llm.calls == []


def test_an_unreadable_log_is_503(signing_key, token):
    h = RefineHarness(signing_key)
    h.log.fail_get = True
    assert h.refine(body(), token).status_code == 503 and h.llm.calls == []


def test_refine_needs_a_token(signing_key):
    h = RefineHarness(signing_key)
    h.prior()
    assert h.refine(body()).status_code == 401 and h.llm.calls == []


@pytest.mark.parametrize(
    "bad",
    [{"message": "  "}, {"message": "x" * 501}, {"query_id": "nope"}, {"extra": 1}],
    ids=["blank", "too long", "not a uuid", "unknown field"],
)
def test_a_bad_body_is_422(signing_key, token, bad):
    h = RefineHarness(signing_key)
    assert h.refine(body() | bad, token).status_code == 422


def test_refine_counts_toward_the_daily_cap(signing_key, token):
    h = RefineHarness(signing_key)
    h.prior()  # one query today
    h.log.preload(8)  # nine
    assert h.refine(body(), token).json()["status"] == "ok"  # ten
    capped = h.refine(body(), token).json()
    assert (capped["status"], capped["reason"], capped["message"]) == (
        "ai_resting",
        "daily_cap",
        CAPPED,
    )
    row = h.log.rows[-1]
    assert (row["status"], row["route"], row["refines"]) == ("capped", "refine", str(PRIOR_ID))
    assert len(h.llm.calls) == 1


def test_a_near_me_search_asks_for_the_location_again(signing_key, token):
    h = RefineHarness(signing_key)
    h.prior(filters={"radius_km": 1.609344, "price_max": 40})  # the point was never stored
    b = h.refine(body(), token).json()
    assert (b["status"], b["missing"], b["results"]) == ("needs_clarification", ["location"], None)
    assert h.backend.calls == []


def test_a_near_me_search_with_the_location_again_is_searched_but_not_logged(signing_key, token):
    h = RefineHarness(
        signing_key, llm=FakeLLM(groq=[change(distance_change="closer")], cloudflare=[])
    )
    h.prior(filters={"radius_km": 1.609344, "price_max": 40})
    b = h.refine(body("somewhere closer", location=WATERLOO), token).json()
    assert (b["status"], b["location_source"]) == ("ok", "device")
    ((filters, _),) = h.backend.calls
    assert (filters["lat"], filters["lon"]) == (51.5031, -0.1132)
    assert filters["radius_km"] == pytest.approx(0.536, abs=0.001)
    logged = json.dumps([h.log.rows[-1], h.log.results])
    for secret in ("51.5031", "-0.1132", "device"):
        assert secret not in logged, secret


def test_a_failed_search_still_answers_the_refine(signing_key, token):
    h = RefineHarness(signing_key, backend=FakeSearch(fail=True))
    h.prior()
    b = h.refine(body(), token).json()
    assert (b["status"], b["results"]) == ("ok", None)
    assert h.log.rows[-1]["refines"] == str(PRIOR_ID)
