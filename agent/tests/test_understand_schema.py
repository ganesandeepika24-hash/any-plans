"""UnderstoodQuery (what the model may say) and SearchFilters (what search_items takes)."""

import json
import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.understand.schema import SearchFilters, UnderstoodQuery, strict_json_schema
from tests.understand_support import REPO, reply, when

MIGRATION_010 = (REPO / "db" / "migrations" / "010_search_items.sql").read_text()


def _sql_array(name: str) -> list[str]:
    body = re.search(rf"{name} constant text\[\] := array\[(.*?)\];", MIGRATION_010, re.S)
    return re.findall(r"'([^']+)'", body.group(1))


# The contract with search_items ------------------------------------------------


def test_search_filters_has_exactly_search_items_keys():
    assert set(SearchFilters.model_fields) == set(_sql_array("allowed"))


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
        "array",
        "string",
    ],
)
def test_anything_else_is_refused(text):
    with pytest.raises(ValidationError):
        UnderstoodQuery.model_validate_json(text)


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
