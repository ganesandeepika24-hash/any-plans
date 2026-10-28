"""Refine's merge rules (stage 1 step 2, decision 0018): what a follow-up changes in an
earlier search, and what it keeps. No model: the replies are hand-written."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.understand.node import AI_RESTING, Understander
from app.understand.refine import Prior, RefineSteps, apply
from app.understand.resolve import KM_PER_MILE, DateRanges, Point
from app.understand.schema import RefineReply
from tests.understand_support import (
    FakeLLM,
    FakeStore,
    RecordingTracer,
    entries,
    london,
    migration_config,
    reply,
    when,
)

CITY = london()
NOW = datetime.fromisoformat("2026-10-27T12:00:00+00:00")
STEPS = RefineSteps.model_validate(migration_config()["refine_steps"])
RANGES = DateRanges.model_validate(migration_config()["date_ranges"])
SOHO = next(a for a in CITY.areas if a.id == "london-soho")
CAMDEN = next(a for a in CITY.areas if a.id == "london-camden")
HOME = Point(51.5031, -0.1132, "device")
WEEKEND = {"date_from": "2026-10-30T17:00:00+00:00", "date_to": "2026-11-02T05:00:00+00:00"}
MUSIC_UNDER_40 = Prior(WEEKEND | {"categories": ["Music"], "price_max": 40}, "live music")
COMEDY_IN_SOHO = Prior(
    {"categories": ["Comedy"], "lat": SOHO.lat, "lon": SOHO.lon, "radius_km": 3 * KM_PER_MILE},
    "comedy",
)
NEAR_ME = Prior({"radius_km": KM_PER_MILE, "price_max": 30}, "jazz", vibe="Chilled", who="Date")


def follow_up(price_change=None, distance_change=None, **fields) -> RefineReply:
    text = reply(**fields)[:-1] + (
        f', "price_change": {_j(price_change)}, "distance_change": {_j(distance_change)}}}'
    )
    return RefineReply.model_validate_json(text)


def _j(v):
    return "null" if v is None else f'"{v}"'


def refine(prior, location=None, **reply_fields):
    return apply(prior, follow_up(**reply_fields), NOW, CITY, STEPS, location, RANGES)


def test_the_steps_shipped_take_the_seed_docs_examples():
    assert STEPS == RefineSteps(cheaper=0.5, closer=0.3333333333, min_radius_km=0.5)


# Price ---------------------------------------------------------------------------


def test_cheaper_halves_the_limit_and_keeps_everything_else():
    r = refine(MUSIC_UNDER_40, price_change="cheaper").resolution
    f = r.filters.to_json()
    assert f == WEEKEND | {"categories": ["Music"], "price_max": 20}
    assert r.query_text == "live music" and r.missing == () and r.notes == ()


def test_cheaper_drops_a_floor_above_the_new_limit():
    prior = Prior({"price_min": 25, "price_max": 40}, None)
    assert refine(prior, price_change="cheaper").resolution.filters.price_min is None


def test_cheaper_with_no_earlier_limit_asks_for_a_budget():
    r = refine(COMEDY_IN_SOHO, price_change="cheaper").resolution
    assert r.missing == ("budget",)


def test_cheaper_than_free_says_so():
    prior = Prior({"price_max": 0, "include_unpriced": False}, None)
    r = refine(prior, price_change="cheaper").resolution
    assert [n.code for n in r.notes] == ["already_free"] and r.filters.price_max == 0


def test_pricier_doubles_the_limit_or_says_there_was_none():
    assert refine(MUSIC_UNDER_40, price_change="pricier").resolution.filters.price_max == 80
    r = refine(COMEDY_IN_SOHO, price_change="pricier").resolution
    assert [n.code for n in r.notes] == ["no_price_limit"]


def test_a_stated_price_replaces_the_earlier_one():
    r = refine(MUSIC_UNDER_40, price_max=15).resolution
    assert (r.filters.price_min, r.filters.price_max) == (None, 15)
    assert refine(MUSIC_UNDER_40, free=True).resolution.filters.price_max == 0


def test_a_vague_new_budget_asks():
    assert refine(MUSIC_UNDER_40, unclear=["budget"]).resolution.missing == ("budget",)


# Distance ------------------------------------------------------------------------


def test_closer_takes_a_third_of_the_radius_around_the_same_centre():
    f = refine(COMEDY_IN_SOHO, distance_change="closer").resolution.filters
    assert (f.lat, f.lon) == (SOHO.lat, SOHO.lon)
    assert f.radius_km == pytest.approx(KM_PER_MILE, rel=1e-6)
    assert f.categories == ["Comedy"]


def test_closer_stops_at_the_smallest_radius():
    prior = Prior({"lat": SOHO.lat, "lon": SOHO.lon, "radius_km": 1.0}, None)
    r = refine(prior, distance_change="closer").resolution
    assert r.filters.radius_km == 0.5 and [n.code for n in r.notes] == ["radius_floor"]


def test_further_triples_the_radius_up_to_the_furthest():
    f = refine(COMEDY_IN_SOHO, distance_change="further").resolution.filters
    assert f.radius_km == pytest.approx(9 * KM_PER_MILE, rel=1e-6)
    prior = Prior({"lat": SOHO.lat, "lon": SOHO.lon, "radius_km": 60.0}, None)
    assert refine(prior, distance_change="further").resolution.filters.radius_km == 100


def test_closer_with_no_earlier_place_uses_the_users_point_or_asks():
    r = refine(MUSIC_UNDER_40, distance_change="closer").resolution
    assert r.missing == ("location",) and r.filters.lat is None
    r = refine(MUSIC_UNDER_40, HOME, distance_change="closer").resolution
    assert (r.filters.lat, r.used_location, r.location_source) == (HOME.lat, True, "device")
    assert r.filters.radius_km == pytest.approx(KM_PER_MILE / 3, rel=1e-6)


def test_a_stated_distance_keeps_the_centre():
    f = refine(COMEDY_IN_SOHO, distance={"value": 2, "unit": "km"}).resolution.filters
    assert (f.lat, f.lon, f.radius_km) == (SOHO.lat, SOHO.lon, 2)


def test_a_new_place_replaces_the_centre():
    f = refine(COMEDY_IN_SOHO, place="Camden").resolution.filters
    assert (f.lat, f.lon, f.radius_km) == (CAMDEN.lat, CAMDEN.lon, CAMDEN.default_radius_km)


# The user's own point, never stored --------------------------------------------------


def test_a_near_me_search_needs_the_location_again():
    r = refine(NEAR_ME, price_change="cheaper").resolution
    assert r.missing == ("location",)
    assert r.filters.lat is None and r.filters.radius_km is None  # all three or none
    assert r.filters.price_max == 15


def test_a_near_me_search_with_the_location_again_keeps_its_radius():
    r = refine(NEAR_ME, HOME, distance_change="closer").resolution
    assert (r.filters.lat, r.filters.lon) == (HOME.lat, HOME.lon)
    assert r.filters.radius_km == pytest.approx(0.536, abs=0.001)
    assert (r.used_location, r.location_source, r.missing) == (True, "device", ())


def test_an_area_search_ignores_a_location_it_didnt_ask_for():
    r = refine(COMEDY_IN_SOHO, HOME, price_change="pricier").resolution
    assert (r.filters.lat, r.used_location) == (SOHO.lat, False)


# Dates, categories, text, vibe and who -----------------------------------------------


def test_a_new_day_replaces_the_dates():
    f = refine(MUSIC_UNDER_40, when=when("weekday", weekday="sat")).resolution.filters
    assert (f.date_from.isoformat(), f.date_to.isoformat()) == (
        "2026-10-31T05:00:00+00:00",
        "2026-11-01T05:00:00+00:00",
    )
    assert f.price_max == 40 and f.categories == ["Music"]


def test_a_vague_new_date_asks_and_drops_the_old_one():
    r = refine(MUSIC_UNDER_40, unclear=["date"]).resolution
    assert r.missing == ("date",) and r.filters.date_from is None


def test_new_categories_and_text_replace_the_old():
    r = refine(MUSIC_UNDER_40, categories=["Comedy"], keywords="stand-up").resolution
    assert r.filters.categories == ["Comedy"] and r.query_text == "stand-up"


def test_vibe_and_who_are_kept_unless_changed():
    r = refine(NEAR_ME, HOME, price_change="cheaper")
    assert (r.vibe, r.who) == ("Chilled", "Date")
    r = refine(NEAR_ME, HOME, vibe="Lively")
    assert (r.vibe, r.who) == ("Lively", "Date")


def test_a_follow_up_that_changes_nothing_keeps_the_search():
    r = refine(MUSIC_UNDER_40).resolution
    assert r.filters.to_json() == MUSIC_UNDER_40.filters and r.missing == ()


@pytest.mark.parametrize(
    "steps",
    [
        {"cheaper": 1, "closer": 0.5, "min_radius_km": 0.5},
        {"cheaper": 0.5, "closer": 0, "min_radius_km": 0.5},
        {"cheaper": 0.5, "closer": 0.5, "min_radius_km": 0},
        {"cheaper": 0.5, "closer": 0.5},
    ],
)
def test_steps_that_wouldnt_change_anything_are_refused(steps):
    with pytest.raises(ValidationError):
        RefineSteps.model_validate(steps)


# The node --------------------------------------------------------------------------


def refiner(llm, store=None):
    return Understander(store or FakeStore(), llm, RecordingTracer(), clock=lambda: NOW)


def cheaper_text() -> str:
    return follow_up(price_change="cheaper").model_dump_json()


@pytest.mark.anyio
async def test_the_node_asks_with_the_refine_prompt_and_schema():
    llm = FakeLLM(groq=[cheaper_text()], cloudflare=[])
    result = await refiner(llm).refine("make it cheaper", MUSIC_UNDER_40)
    assert result.status == "ok" and result.filters.price_max == 20
    (call,) = llm.calls
    assert call["system"] == migration_config()["refine_prompt"]["system"]
    assert call["user"] == "make it cheaper"  # the follow-up only: the earlier search stays in code
    assert "price_change" in call["schema"]["properties"]
    assert result.config_versions == {
        "llm_understand": 1,
        "refine_prompt": 1,
        "date_ranges": 1,
        "refine_steps": 1,
    }
    assert result.understood.price_change == "cheaper"


@pytest.mark.anyio
async def test_a_search_reply_isnt_a_refine_reply():
    llm = FakeLLM(groq=[reply()], cloudflare=[reply()])  # no price_change or distance_change
    result = await refiner(llm).refine("make it cheaper", MUSIC_UNDER_40)
    assert result.status == "ai_resting" and {a.outcome for a in result.attempts} == {"schema"}


@pytest.mark.anyio
async def test_no_steps_config_rests_without_calling_a_model():
    store = FakeStore(entries())
    del store.entries["refine_steps"]
    llm = FakeLLM(groq=[cheaper_text()], cloudflare=[])
    result = await refiner(llm, store).refine("make it cheaper", MUSIC_UNDER_40)
    assert (result.status, llm.calls) == ("ai_resting", [])
    assert result.message.startswith(AI_RESTING)


@pytest.mark.anyio
async def test_a_refine_that_needs_the_location_asks_for_it():
    llm = FakeLLM(groq=[cheaper_text()], cloudflare=[])
    result = await refiner(llm).refine("make it cheaper", NEAR_ME)
    assert (result.status, result.missing) == ("needs_clarification", ("location",))
    assert result.questions[0].reason == "location"
    assert (result.vibe, result.who) == ("Chilled", "Date")


# Recurring days (decision 0020) -----------------------------------------------------

SATURDAYS = [
    ["2026-10-31T05:00:00+00:00", "2026-11-01T05:00:00+00:00"],
    ["2026-11-07T05:00:00+00:00", "2026-11-08T05:00:00+00:00"],
]
SATURDAY_JAZZ = Prior(
    {
        "date_from": SATURDAYS[0][0],
        "date_to": SATURDAYS[-1][1],
        "windows": SATURDAYS,
        "price_max": 40,
    },
    "jazz",
)


def test_a_change_that_isnt_the_date_keeps_the_saturdays():
    f = refine(SATURDAY_JAZZ, price_change="cheaper").resolution.filters
    assert f.price_max == 20 and f.to_json()["windows"] == SATURDAYS


def test_a_new_day_replaces_the_saturdays():
    f = refine(SATURDAY_JAZZ, when=when("weekday", weekday="fri")).resolution.filters
    assert f.windows is None
    assert f.date_from.isoformat() == "2026-10-30T05:00:00+00:00"


def test_saturdays_in_march_replace_a_weekend():
    f = refine(MUSIC_UNDER_40, when=when("every_weekday", "sat", month=3)).resolution.filters
    assert [a.date().isoformat() for a, _ in f.windows] == [
        "2027-03-06",
        "2027-03-13",
        "2027-03-20",
        "2027-03-27",
    ]
    assert f.price_max == 40 and f.categories == ["Music"]


def test_any_saturday_as_a_follow_up_asks_with_choices():
    r = refine(MUSIC_UNDER_40, when=when("every_weekday", "sat")).resolution
    assert r.missing == ("date",) and r.filters.date_from is None
    assert r.date_choices == ("Next 4 Saturdays", "This month", "Pick a month")


@pytest.mark.anyio
async def test_no_date_ranges_config_rests_without_calling_a_model():
    store = FakeStore(entries())
    del store.entries["date_ranges"]
    llm = FakeLLM(groq=[cheaper_text()], cloudflare=[])
    result = await refiner(llm, store).refine("make it cheaper", MUSIC_UNDER_40)
    assert (result.status, llm.calls) == ("ai_resting", [])
