"""Party size and the per-ticket price cap (decision 0024). The model reads the numbers;
code divides, decides whether to ask, and writes the note. No model here: the replies
are hand-written, as a good model would give them."""

import json
import re

import pytest
from pydantic import ValidationError

from app.understand.clarify import PARTY_SIZE_CHOICES, questions
from app.understand.refine import Prior
from app.understand.resolve import MISSING_ORDER, Budget, per_ticket, pounds, resolve
from app.understand.schema import SearchFilters, UnderstoodQuery
from tests.test_auth import _claims, _token
from tests.test_parse_route import USER
from tests.test_refine import NOW, WEEKEND, refine
from tests.test_refine_route import PRIOR_ID, RefineHarness, change
from tests.test_refine_route import body as refine_body
from tests.test_search_route import Harness
from tests.understand_support import FakeLLM, london, migration_config, reply, when

CITY = london()
COMEDY = {"keywords": "comedy", "categories": ["Comedy"], "when": when("this_weekend")}


@pytest.fixture(scope="module")
def signing_key():
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def token(signing_key):
    return _token(signing_key, _claims(sub=str(USER)))


def understood(**fields) -> UnderstoodQuery:
    return UnderstoodQuery.model_validate_json(reply(**COMEDY | fields))


def run(**fields):
    return resolve(understood(**fields), NOW, CITY)


def codes(r) -> list[str]:
    return [n.code for n in r.notes]


def cap_note(r) -> str | None:
    return next((n.message for n in r.notes if n.code == "ticket_cap"), None)


# When to ask: only when the answer changes the search --------------------------------


@pytest.mark.parametrize(
    ("scope", "size", "missing", "price_max"),
    [
        ("each", None, (), 20),  # per person: no division, nothing to ask
        ("each", 5, (), 20),
        ("total", 1, (), 20),  # one person: the total is the ticket
        ("total", 5, (), 4),  # £20 between five
        (None, None, (), 20),  # a plain "under £20" is per ticket (decision 0012)
        (None, 1, (), 20),
        (None, 5, ("price_basis",), None),  # £20 each, or for all five?
        ("total", None, ("party_size",), None),  # a total for how many?
    ],
)
def test_the_cap_or_the_question(scope, size, missing, price_max):
    r = run(price_max=20, price_scope=scope, party_size=size)
    assert r.missing == missing
    assert r.filters.price_max == price_max
    assert r.filters.party_size == size  # kept either way, for the panel and the URL


def test_a_total_divides_the_floor_too():
    r = run(price_min=50, price_max=100, price_scope="total", party_size=4)
    assert (r.filters.price_min, r.filters.price_max) == (12.5, 25)


@pytest.mark.parametrize(
    ("total", "size", "each"),
    [(100, 5, 20), (60, 7, 8.57), (100, 3, 33.33), (100, 6, 16.66), (0.1, 3, 0.03)],
)
def test_a_share_rounds_down_to_the_penny(total, size, each):
    assert per_ticket(total, size) == each
    assert per_ticket(total, size) * size <= total  # the group never goes over


def test_free_or_no_budget_with_a_party_needs_no_question_or_note():
    free = run(free=True, party_size=5)
    assert (free.missing, free.filters.price_max, codes(free)) == ((), 0.0, ["default_area"])
    none = run(party_size=5)
    assert (none.missing, none.filters.price_max, codes(none)) == ((), None, ["default_area"])
    assert none.filters.party_size == 5 and none.budget is None


def test_cheap_for_a_party_still_asks_for_a_budget_only():
    assert run(unclear=["budget"], party_size=5).missing == ("budget",)


def test_an_inverted_total_is_one_note():
    r = run(price_min=100, price_max=50, price_scope="total", party_size=5)
    assert codes(r) == ["default_area", "price_range_inverted"]
    assert r.filters.price_max is None


def test_the_stated_budget_is_kept_before_division():
    r = run(price_max=100, price_scope="total", party_size=5)
    assert r.budget == Budget(None, 100, "total")
    assert r.filters.price_max == 20


# The note ----------------------------------------------------------------------------


def test_the_note_gives_the_cap_for_the_party():
    r = run(price_max=100, price_scope="total", party_size=5)
    assert cap_note(r) == "Up to £20 each for 5 people (tickets only)"
    assert codes(r) == ["default_area", "ticket_cap"]
    assert cap_note(run(price_max=60, price_scope="total", party_size=7)) == (
        "Up to £8.57 each for 7 people (tickets only)"
    )
    assert cap_note(run(price_max=20, price_scope="each", party_size=5)) == (
        "Up to £20 each for 5 people (tickets only)"
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"price_max": 20},  # no party size
        {"price_max": 20, "party_size": 1},  # one person: "each" says nothing new
        {"price_max": 20, "party_size": 5},  # asking first, so no cap yet
        {"price_min": 20, "price_scope": "each", "party_size": 5},  # a floor isn't a cap
    ],
)
def test_no_note_without_a_cap_for_two_or_more(fields):
    assert cap_note(run(**fields)) is None


def test_pounds_drops_whole_pence():
    assert [pounds(x) for x in (20, 20.0, 8.57, 8.5, 0.03)] == [
        "£20",
        "£20",
        "£8.57",
        "£8.50",
        "£0.03",
    ]


# The questions: one plain sentence, with taps -----------------------------------------


def test_the_basis_question_names_the_amount_and_the_party():
    u = understood(price_max=100, party_size=5)
    r = resolve(u, NOW, CITY)
    (q,) = questions(r.missing, u, CITY.name, (), r.budget, r.filters.party_size)
    assert q.reason == "price_basis"
    assert q.question == "Is £100 for each person, or for all 5 of you?"
    assert q.choices == ("Each person", "All 5 of us") and q.groups == ()


def test_the_party_size_question_offers_numbers():
    u = understood(price_max=100, price_scope="total")
    r = resolve(u, NOW, CITY)
    (q,) = questions(r.missing, u, CITY.name, (), r.budget, r.filters.party_size)
    assert q.reason == "party_size"
    assert q.question == "How many people is the £100 for?"
    assert q.choices == PARTY_SIZE_CHOICES == tuple(f"{n} people" for n in range(2, 7))


def test_party_size_comes_after_price_basis_in_the_fixed_order():
    assert MISSING_ORDER == (
        "location",
        "date",
        "budget",
        "price_basis",
        "party_size",
        "event_date",
        "category",
    )
    r = run(price_max=100, price_scope="total", unclear=["date"], when=None)
    assert r.missing == ("date", "party_size")


# Taps and typed answers through /search ----------------------------------------------
#
# The app sends the search again with the answer appended (docs/api.md section 8), and
# the parser reads it all again. These replies are what the model gives for each text.

ASK = reply(**COMEDY, price_max=100, party_size=5)


@pytest.mark.parametrize(
    ("answer", "scope", "cap"),
    [
        ("All 5 of us", "total", 20),  # the tap
        ("Each person", "each", 100),  # the other tap
        ("it's per head", "each", 100),  # typed
        ("for everyone, all in", "total", 20),  # typed
    ],
)
def test_an_answer_to_the_basis_question_searches(signing_key, token, answer, scope, cap):
    query = "Comedy this weekend, five of us, about a hundred quid"
    answered = reply(**COMEDY, price_max=100, price_scope=scope, party_size=5)
    h = Harness(signing_key, llm=FakeLLM(groq=[ASK, answered], cloudflare=[]))
    first = h.post({"query": query}, token).json()
    assert (first["status"], first["missing"]) == ("needs_clarification", ["price_basis"])
    assert first["results"] is None and h.backend.calls == []
    tap = first["questions"][0]["choices"]
    assert tap == ["Each person", "All 5 of us"]

    second = h.post({"query": f"{query}. {answer}"}, token).json()
    assert second["status"] == "ok"
    assert (second["filters"]["price_max"], second["filters"]["party_size"]) == (cap, 5)
    assert {"code": "ticket_cap", "message": f"Up to £{cap} each for 5 people (tickets only)"} in (
        second["notes"]
    )
    assert h.llm.calls[1]["user"] == f"{query}. {answer}"
    ((filters, _),) = h.backend.calls
    assert filters["party_size"] == 5  # search_items takes it (026) but doesn't filter on it
    row = h.log.rows[-1]
    assert row["budget"] == {"price_min": None, "price_max": 100, "basis": scope}


def test_a_typed_party_size_divides_the_total(signing_key, token):
    asked = reply(**COMEDY, price_max=100, price_scope="total")
    answered = reply(**COMEDY, price_max=100, price_scope="total", party_size=4, who="Friends")
    h = Harness(signing_key, llm=FakeLLM(groq=[asked, answered], cloudflare=[]))
    first = h.post({"query": "Comedy this weekend, £100 all in"}, token).json()
    assert first["missing"] == ["party_size"]
    assert first["questions"][0]["question"] == "How many people is the £100 for?"
    second = h.post({"query": "Comedy this weekend, £100 all in. me and 3 mates"}, token).json()
    assert second["status"] == "ok" and second["filters"]["price_max"] == 25


def test_an_answer_that_still_doesnt_say_asks_again(signing_key, token):
    # "hmm, not sure": the model gives no basis, so the same question comes back rather
    # than a guess.
    h = Harness(signing_key, llm=FakeLLM(groq=[ASK, ASK], cloudflare=[]))
    query = "Comedy this weekend, five of us, about a hundred quid"
    h.post({"query": query}, token)
    again = h.post({"query": f"{query}. hmm, not sure"}, token).json()
    assert again["missing"] == ["price_basis"] and h.backend.calls == []


# Follow-ups ----------------------------------------------------------------------------

FIVE_FOR_100 = {
    "filters": WEEKEND | {"categories": ["Comedy"], "price_max": 20, "party_size": 5},
    "budget": {"price_min": None, "price_max": 100, "basis": "total"},
}


def prior(filters, budget=None, vibe=None):
    return Prior(filters, "comedy", vibe, None, budget)


def test_more_chilled_changes_the_vibe_and_keeps_the_party_and_cap():
    r = refine(prior(**FIVE_FOR_100), vibe="Chilled")
    f = r.resolution.filters.to_json()
    assert f == FIVE_FOR_100["filters"]
    assert r.vibe == "Chilled" and r.resolution.missing == ()
    assert r.resolution.budget == Budget(None, 100, "total")
    assert cap_note(r.resolution) == "Up to £20 each for 5 people (tickets only)"


def test_a_new_party_size_divides_the_stored_total_again():
    r = refine(prior(**FIVE_FOR_100), party_size=6).resolution
    assert (r.filters.price_max, r.filters.party_size) == (16.66, 6)
    assert cap_note(r) == "Up to £16.66 each for 6 people (tickets only)"


def test_a_new_party_size_leaves_a_per_person_cap_alone():
    each = prior(
        WEEKEND | {"price_max": 20, "party_size": 5},
        {"price_min": None, "price_max": 20, "basis": "each"},
    )
    r = refine(each, party_size=6).resolution
    assert (r.filters.price_max, r.filters.party_size, r.missing) == (20, 6, ())


def test_a_party_for_a_budget_that_never_said_which_asks():
    solo = prior(WEEKEND | {"price_max": 30}, {"price_min": None, "price_max": 30, "basis": None})
    r = refine(solo, party_size=6).resolution
    assert r.missing == ("price_basis",) and r.filters.price_max is None
    (q,) = questions(r.missing, understood(), CITY.name, (), r.budget, r.filters.party_size)
    assert q.question == "Is £30 for each person, or for all 6 of you?"


def test_saying_the_basis_later_uses_the_stored_amount():
    asked = prior(
        WEEKEND | {"party_size": 5}, {"price_min": None, "price_max": 100, "basis": "each"}
    )
    r = refine(asked, price_scope="total").resolution  # "actually that's for all of us"
    assert r.filters.price_max == 20


def test_a_new_total_uses_the_earlier_party_size():
    r = refine(prior(**FIVE_FOR_100), price_max=150, price_scope="total").resolution
    assert (r.filters.price_max, r.budget) == (30, Budget(None, 150, "total"))


def test_cheaper_lowers_the_ticket_cap_which_is_then_per_person():
    r = refine(prior(**FIVE_FOR_100), price_change="cheaper").resolution
    assert r.filters.price_max == 10 and r.budget == Budget(None, 10, "each")
    assert cap_note(r) == "Up to £10 each for 5 people (tickets only)"


def test_an_earlier_search_with_no_stored_budget_keeps_its_cap():
    # Logged before 026: filters only. Its cap stays, whatever the new party size.
    r = refine(prior(WEEKEND | {"price_max": 40}), party_size=4).resolution
    assert (r.filters.price_max, r.filters.party_size, r.missing) == (40, 4, ())


def test_free_in_a_follow_up_drops_the_stored_budget():
    r = refine(prior(**FIVE_FOR_100), free=True).resolution
    assert (r.filters.price_max, r.budget, cap_note(r)) == (0.0, None, None)


def test_the_refine_route_reads_the_stored_budget(signing_key, token):
    h = RefineHarness(signing_key, llm=FakeLLM(groq=[change(party_size=6)], cloudflare=[]))
    h.prior(filters=FIVE_FOR_100["filters"], budget=FIVE_FOR_100["budget"])
    b = h.refine(refine_body("it's 6 of us now"), token).json()
    assert b["status"] == "ok" and b["filters"]["price_max"] == 16.66
    row = h.log.rows[-1]
    assert (row["refines"], row["budget"]) == (str(PRIOR_ID), FIVE_FOR_100["budget"])


def test_the_refine_route_answers_more_chilled(signing_key, token):
    h = RefineHarness(signing_key, llm=FakeLLM(groq=[change(vibe="Chilled")], cloudflare=[]))
    h.prior(filters=FIVE_FOR_100["filters"], budget=FIVE_FOR_100["budget"])
    b = h.refine(refine_body("make it more chilled"), token).json()
    assert (b["status"], b["vibe"], b["filters"]) == ("ok", "Chilled", FIVE_FOR_100["filters"])


# Shapes and prompts ------------------------------------------------------------------


def test_party_size_is_optional_so_v6_replies_still_validate():
    v6 = json.loads(reply())
    v6.pop("party_size", None)
    assert UnderstoodQuery.model_validate(v6).party_size is None


@pytest.mark.parametrize("bad", [0, -1, 1001, 2.5, "5"])
def test_a_bad_party_size_is_refused(bad):
    with pytest.raises(ValidationError):
        UnderstoodQuery.model_validate_json(reply(party_size=bad))
    if not isinstance(bad, str | float):
        with pytest.raises(ValidationError):
            SearchFilters(party_size=bad)


def test_both_prompts_read_party_size_and_loose_amounts():
    config = migration_config()
    for key in ("understand_prompt", "refine_prompt"):
        prompt = config[key]["system"]
        assert re.search(r"^- party_size:", prompt, re.M), key
        for phrase in (
            '"five of us"',
            '"me and 3 mates"',
            '"about a hundred quid"',
            '"a ton"',
            '"under a tenner"',
            '"all in"',
            '"per head"',
            "Never divide or multiply",
        ):
            assert phrase in prompt, (key, phrase)
    assert '"there\'ll be six"' in config["understand_prompt"]["system"]  # a typed answer
    assert '"it\'s 6 of us now"' in config["refine_prompt"]["system"]
    assert '"more chilled"' in config["refine_prompt"]["system"]
