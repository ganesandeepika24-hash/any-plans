"""UnderstoodQuery (what the model may say) and SearchFilters (what search_items takes)."""

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from app.understand.resolve import DateRanges
from app.understand.schema import (
    Category,
    SearchFilters,
    UnderstoodQuery,
    Vibe,
    When,
    Who,
    strict_json_schema,
)
from tests.understand_support import REPO, migration_config, reply, when

# The latest migration that creates search_items: 010, replaced by 022 (decision 0022).
SEARCH_ITEMS = [
    p.read_text()
    for p in sorted((REPO / "db" / "migrations").glob("*.sql"))
    if re.search(r"create (or replace )?function public\.search_items\(", p.read_text())
][-1]


def _sql_array(name: str) -> list[str]:
    body = re.search(rf"{name} constant text\[\] := array\[(.*?)\];", SEARCH_ITEMS, re.S)
    return re.findall(r"'([^']+)'", body.group(1))


def search_items_columns() -> list[str]:
    body = re.search(r"returns table \((.*?)\)\nlanguage", SEARCH_ITEMS, re.S)
    return re.findall(r"^\s*(\w+) ", body.group(1), re.M)


# The contract with search_items ------------------------------------------------


def test_search_filters_has_exactly_search_items_keys():
    # windows included: search_items applies it since 024.
    assert set(SearchFilters.model_fields) == set(_sql_array("allowed"))


def test_result_cards_take_their_fields_from_search_items_columns():
    from app.search import ResultOut

    columns = search_items_columns()
    assert {"confidence", "confidence_label", "lat", "lon"} <= set(columns)
    # rank is the agent's own; venue_id and similarity stay in the database.
    assert set(ResultOut.model_fields) - {"rank"} == set(columns) - {"venue_id", "similarity"}


def test_the_public_search_returns_the_agents_card_fields():
    # Signed-out filter search (migration 023, decision 0023) returns what the agent's
    # cards carry, in search_items' order: the app renders both with one card.
    from app.search import ResultOut

    sql = (REPO / "db" / "migrations" / "023_search_items_public.sql").read_text()
    body = re.search(r"returns table \((.*?)\)\nlanguage", sql, re.S)
    columns = re.findall(r"^\s*(\w+) ", body.group(1), re.M)
    assert columns == [c for c in search_items_columns() if c not in ("venue_id", "similarity")]
    assert set(columns) == set(ResultOut.model_fields) - {"rank"}
    assert "search_items(filters, null)" in sql  # no embedding: filters only


def test_categories_match_search_items():
    from app.understand.schema import Category

    assert set(Category.__args__) == set(_sql_array("known_categories"))


def test_to_json_sends_only_set_keys_with_offsets():
    filters = SearchFilters(
        date_from=datetime(2026, 11, 6, 17, tzinfo=UTC),
        date_to=datetime(2026, 11, 9, 5, tzinfo=timezone(timedelta(hours=1))),
        categories=["Music"],
    )
    out = filters.to_json()
    assert out == {
        "date_from": "2026-11-06T17:00:00+00:00",
        "date_to": "2026-11-09T05:00:00+01:00",
        "categories": ["Music"],
    }
    offset = r"(Z|[+-][0-9]{2}(:?[0-9]{2})?)$"  # search_items' offset_pattern
    assert all(re.search(offset, out[k]) for k in ("date_from", "date_to"))


@pytest.mark.parametrize(
    "fields",
    [
        {"date_from": datetime(2026, 11, 6)},  # naive
        {
            "date_from": datetime(2026, 11, 7, tzinfo=UTC),
            "date_to": datetime(2026, 11, 6, tzinfo=UTC),
        },
        {"categories": []},
        {"categories": ["Jazz"]},
        {"price_min": -1},
        {"price_min": 20, "price_max": 10},
        {"lat": 51.5, "lon": -0.1},
        {"lat": 51.5, "lon": -0.1, "radius_km": 0},
        {"lat": 51.5, "lon": -0.1, "radius_km": 101},
        {"lat": 91, "lon": -0.1, "radius_km": 1},
        {"limit": 0},
        {"limit": 101},
        {"bogus": 1},
    ],
    ids=[
        "naive date",
        "from after to",
        "no categories",
        "unknown category",
        "negative price",
        "min over max",
        "point without radius",
        "zero radius",
        "radius over 100",
        "latitude out of range",
        "limit 0",
        "limit 101",
        "unknown key",
    ],
)
def test_search_filters_refuses_what_search_items_refuses(fields):
    with pytest.raises(ValidationError):
        SearchFilters(**fields)


# What the model may return --------------------------------------------------------


def test_a_full_reply_validates():
    q = UnderstoodQuery.model_validate_json(
        reply(
            keywords="jazz",
            categories=["Music"],
            when=when("weekday", weekday="fri"),
            place="Soho",
            distance={"value": 2, "unit": "mi"},
            price_max=20,
        )
    )
    assert (q.keywords, q.when.weekday, q.distance.unit, q.price_max) == ("jazz", "fri", "mi", 20)


def test_a_date_may_carry_its_weekday_and_unclear_parts_dedupe():
    q = UnderstoodQuery.model_validate_json(
        reply(
            when=when("date", weekday="thu", qualifier="next", month=11, day=5),
            unclear=["budget", "budget", "category"],
            event="  ",
        )
    )
    assert (q.when.weekday, q.when.qualifier, q.when.day) == ("thu", "next", 5)
    assert (q.unclear, q.event) == (["budget", "category"], None)


def test_blank_strings_and_empty_categories_mean_not_said():
    q = UnderstoodQuery.model_validate_json(reply(keywords="  ", place="", categories=[]))
    assert (q.keywords, q.place, q.categories) == (None, None, None)


def _invalid(**changes) -> str:
    data = json.loads(reply())
    for key, value in changes.items():
        if value is ...:
            del data[key]
        else:
            data[key] = value
    return json.dumps(data)


@pytest.mark.parametrize(
    "text",
    [
        _invalid(extra="x"),
        _invalid(free=...),
        _invalid(free="false"),
        _invalid(free=None),
        _invalid(categories=["Jazz"]),
        _invalid(categories="Music"),
        _invalid(price_max=-5),
        _invalid(price_max="20"),
        _invalid(when=when("weekday")),
        _invalid(when=when("tonight", weekday="fri")),
        _invalid(when=when("date", month=11)),
        _invalid(when=when("date", month=13, day=1)),
        _invalid(when=when("date", month=11.0, day=14)),
        _invalid(when=when("soon")),
        _invalid(when={"kind": "today"}),
        _invalid(distance={"value": 0, "unit": "km"}),
        _invalid(distance={"value": 2, "unit": "miles"}),
        _invalid(keywords="x" * 201),
        _invalid(when=when("weekday", weekday="fri", qualifier="last")),
        _invalid(when=when("tonight", qualifier="next")),
        _invalid(when=when("holiday")),
        _invalid(when=when("holiday", holiday="diwali")),
        _invalid(when=when("date", month=12, day=25, holiday="bank_holiday")),
        _invalid(when=when("holiday", holiday="easter_monday", weekday="mon")),
        _invalid(unclear=["location"]),
        _invalid(unclear=None),
        _invalid(unclear=...),
        _invalid(near_me=...),
        _invalid(price_scope="per person"),
        _invalid(event="x" * 101),
        _invalid(vibe="Romantic"),
        _invalid(vibe=["Chilled"]),
        _invalid(vibe=...),
        _invalid(who="Couple"),
        _invalid(who=...),
        "[]",
        '"jazz tonight"',
    ],
    ids=[
        "extra key",
        "missing field",
        "bool as string",
        "null bool",
        "unknown category",
        "categories not a list",
        "negative price",
        "price as string",
        "weekday kind without weekday",
        "weekday on another kind",
        "date without day",
        "month 13",
        "month as float",
        "unknown kind",
        "when missing parts",
        "zero distance",
        "unknown unit",
        "keywords too long",
        "unknown qualifier",
        "qualifier without weekday",
        "holiday kind without holiday",
        "unknown holiday",
        "holiday on another kind",
        "weekday on a holiday",
        "location isn't the model's to report",
        "null unclear",
        "missing unclear",
        "missing near_me",
        "unknown price scope",
        "event too long",
        "unknown vibe",
        "vibe as a list",
        "missing vibe",
        "unknown who",
        "missing who",
        "array",
        "string",
    ],
)
def test_anything_else_is_refused(text):
    with pytest.raises(ValidationError):
        UnderstoodQuery.model_validate_json(text)


def test_vibe_and_who_take_the_prd_chips():
    q = UnderstoodQuery.model_validate_json(reply(vibe="Outdoors", who="Family with kids"))
    assert (q.vibe, q.who) == ("Outdoors", "Family with kids")
    schema = strict_json_schema(UnderstoodQuery)["properties"]
    assert schema["vibe"]["anyOf"][0]["enum"] == [
        "Chilled",
        "Lively",
        "Cultural",
        "Foodie",
        "Outdoors",
    ]
    assert schema["who"]["anyOf"][0]["enum"] == ["Solo", "Date", "Friends", "Family with kids"]


def test_the_schema_suits_groq_strict_mode():
    """Groq's strict mode: every object closed, every property required, no $ref."""
    schema = strict_json_schema(UnderstoodQuery)
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert set(schema["properties"]) == set(UnderstoodQuery.model_fields)


def test_the_active_prompt_names_every_category_vibe_and_who():
    prompt = migration_config()["understand_prompt"]["system"]
    for value in (*get_args(Category), *get_args(Vibe), *get_args(Who)):
        assert value in prompt, value
    assert "- vibe:" in prompt and "- who:" in prompt


def test_the_active_prompt_reads_the_near_me_phrases_and_a_bare_something():
    prompt = migration_config()["understand_prompt"]["system"]
    for phrase in ('"near me"', '"nearby me"', '"around me"', '"something near me tonight"'):
        assert phrase in prompt, phrase


def test_both_prompts_name_observances_and_half_terms_as_events():
    # Code looks the name up in the calendar (decision 0019); the two prompts share it.
    config = migration_config()
    for key in ("understand_prompt", "refine_prompt"):
        prompt = config[key]["system"]
        for phrase in ('"Mother\'s Day"', '"February half-term"', '"Carnival, Sunday"'):
            assert phrase in prompt, (key, phrase)


def test_both_prompts_name_the_span_kinds_and_no_longer_flag_months_or_seasons():
    # Decision 0020: months, ranges, seasons and recurring days are when kinds now.
    config = migration_config()
    for key in ("understand_prompt", "refine_prompt"):
        prompt = config[key]["system"]
        for phrase in ("every_weekday", "every_weekend", "this_month", "- season:", "- range:"):
            assert phrase in prompt, (key, phrase)
        assert '"in March"' not in prompt.split('"date": a time')[1], key
        assert '"this winter"' not in prompt.split('"date": a time')[1], key


@pytest.mark.parametrize(
    ("kind", "parts"),
    [
        ("month", {}),  # no month
        ("range", {"month": 11, "day": 14}),  # no end
        ("season", {}),  # no season
        ("every_weekday", {}),  # no weekday
        ("every_weekend", {"weekday": "sat"}),  # a weekend has no weekday
        ("every_weekday", {"weekday": "sat", "count": 4, "month": 3}),  # two scopes
        ("weekday", {"weekday": "sat", "count": 4}),  # count without a recurring day
        ("date", {"month": 11, "day": 14, "season": "winter"}),  # season on a date
        ("month", {"month": 3, "day": 1}),  # a month has no day
    ],
)
def test_when_parts_must_match_their_kind(kind, parts):
    with pytest.raises(ValidationError):
        When.model_validate(when(kind, **parts))


def test_a_when_from_an_earlier_prompt_still_validates():
    # v5 replies have none of the fields v6 added.
    old = {k: v for k, v in when("weekday", "sat").items()}
    assert When.model_validate(old).count is None


def test_the_date_ranges_config_validates_and_rejects_impossible_days():
    ranges = DateRanges.model_validate(migration_config()["date_ranges"])
    assert set(ranges.seasons) == {"winter"}  # the others ask until they're defined
    bad = migration_config()["date_ranges"]
    bad["seasons"]["winter"]["end_day"] = 32
    with pytest.raises(ValidationError):
        DateRanges.model_validate(bad)
    bad["seasons"]["winter"] |= {"end_month": 2, "end_day": 30}
    with pytest.raises(ValidationError):
        DateRanges.model_validate(bad)
    with pytest.raises(ValidationError):
        DateRanges.model_validate(migration_config()["date_ranges"] | {"extra": 1})
