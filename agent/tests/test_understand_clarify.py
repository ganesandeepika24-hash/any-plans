"""Asking before searching (step 1.5): the ambiguity, location and clarify-chip rules,
the radius rule and bank holidays. Code decides what's missing from what the model
reported; nothing here calls a model."""

import json
from datetime import date, datetime, timedelta

import pytest

from app.understand import holidays
from app.understand.clarify import CATEGORY_CHOICES, LOCATION_CHOICES, questions
from app.understand.resolve import KM_PER_MILE, MISSING_ORDER, Point, resolve
from app.understand.schema import UnderstoodQuery
from tests.understand_support import REPO, london, reply, when

CITY = london()
TUE_NOON = datetime.fromisoformat("2026-10-27T12:00:00+00:00")  # the eval's "now"
WATERLOO = Point(51.5031, -0.1132)
SOHO = next(a for a in CITY.areas if a.id == "london-soho")
CAMDEN = next(a for a in CITY.areas if a.id == "london-camden")


def understood(**fields) -> UnderstoodQuery:
    return UnderstoodQuery.model_validate_json(reply(**fields))


def run(location=None, now=TUE_NOON, **fields):
    return resolve(understood(**fields), now, CITY, location)


def days(r) -> tuple[str, str] | None:
    f = r.filters
    return None if f.date_from is None else (f.date_from.isoformat(), f.date_to.isoformat())


def day(d: str) -> tuple[str, str]:
    start = date.fromisoformat(d)
    return (f"{start}T05:00:00+00:00", f"{start + timedelta(days=1)}T05:00:00+00:00")


# Weekdays and dates (ambiguity rules 1 to 3) -------------------------------------


def test_a_weekday_named_on_that_day_is_today():
    r = run(when=when("weekday", weekday="tue"))
    assert days(r) == ("2026-10-27T12:00:00+00:00", "2026-10-28T05:00:00+00:00")
    assert r.missing == ()


@pytest.mark.parametrize("qualifier", [None, "this"])
def test_a_bare_or_this_weekday_is_the_coming_one(qualifier):
    r = run(when=when("weekday", weekday="thu", qualifier=qualifier))
    assert days(r) == day("2026-10-29") and r.missing == ()


def test_next_weekday_on_another_day_asks():
    r = run(when=when("weekday", weekday="fri", qualifier="next"), price_max=25)
    assert r.missing == ("date",)
    assert r.filters.date_from is None and r.filters.price_max == 25  # the rest resolves


def test_next_weekday_on_that_same_day_is_a_week_today():
    r = run(when=when("weekday", weekday="tue", qualifier="next"))
    assert days(r) == day("2026-11-03") and r.missing == ()


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        ({"weekday": "thu", "qualifier": "next", "month": 11, "day": 5}, "2026-11-05"),
        ({"weekday": "thu", "qualifier": "this", "month": 10, "day": 29}, "2026-10-29"),
        ({"weekday": "fri", "month": 11, "day": 6}, "2026-11-06"),
        ({"weekday": "sun", "month": 11, "day": 8}, "2026-11-08"),
    ],
    ids=["5 Nov next Thursday", "29 Oct this Thursday", "Friday 6 November", "Sunday 8 Nov"],
)
def test_a_date_and_weekday_that_agree_resolve(parts, expected):
    r = run(when=when("date", **parts))
    assert days(r) == day(expected) and r.missing == ()


@pytest.mark.parametrize(
    "parts",
    [
        {"weekday": "thu", "qualifier": "this", "month": 11, "day": 5},
        {"weekday": "fri", "month": 11, "day": 7},
        {"weekday": "thu", "qualifier": "next", "month": 11, "day": 12},
    ],
    ids=["Bonfire Night this Thursday", "Friday 7 November", "12 Nov next Thursday"],
)
def test_a_date_and_weekday_that_disagree_ask(parts):
    r = run(when=when("date", **parts))
    assert r.missing == ("date",) and r.filters.date_from is None


def test_a_vague_date_asks_and_keeps_the_place():
    r = run(unclear=["date"], place="Shoreditch")  # "two weekends from now, Shoreditch"
    assert r.missing == ("date",)
    assert (r.filters.lat, r.filters.radius_km) == (51.526669, 1.0)


def test_unclear_date_wins_over_a_when_the_model_also_gave():
    r = run(unclear=["date"], when=when("this_weekend"))
    assert r.missing == ("date",) and r.filters.date_from is None


# Events with no date --------------------------------------------------------------


def test_an_event_with_no_date_asks_for_its_date():
    r = run(event="Pride in London", keywords="Pride")
    assert r.missing == ("event_date",)


def test_an_event_with_a_date_resolves():
    r = run(event="Diwali", when=when("date", month=11, day=7), place="Wembley")
    assert r.missing == () and days(r) == day("2026-11-07")


# Budget and price basis (rules 4 and 5) -------------------------------------------


def test_cheap_asks_for_a_budget():
    r = run(unclear=["budget"], when=when("weekday", weekday="sat"), place="Clapham")
    assert r.missing == ("budget",)
    assert r.filters.price_max is None and days(r) == day("2026-10-31")


def test_a_stated_price_beats_a_vague_word():
    r = run(unclear=["budget"], price_max=30)
    assert r.missing == () and r.filters.price_max == 30


def test_a_total_price_asks_whether_it_is_per_person():
    r = run(price_max=60, price_scope="total")
    assert r.missing == ("price_basis",) and r.filters.price_max is None


@pytest.mark.parametrize("scope", ["each", None])
def test_an_each_or_unscoped_price_is_per_ticket(scope):
    r = run(price_max=60, price_scope=scope)
    assert r.missing == () and r.filters.price_max == 60


def test_free_never_asks_about_price():
    r = run(free=True, price_scope="total")
    assert r.missing == () and (r.filters.price_max, r.filters.include_unpriced) == (0, False)


# Category (clarify chips) ----------------------------------------------------------


def test_a_vague_request_asks_for_a_category_and_keeps_date_and_budget():
    # "Something fun this weekend, under £40 each"
    r = run(
        keywords="fun",
        unclear=["category"],
        when=when("this_weekend"),
        price_max=40,
        price_scope="each",
    )
    assert r.missing == ("category",)
    assert days(r) == ("2026-10-30T17:00:00+00:00", "2026-11-02T05:00:00+00:00")
    assert r.filters.price_max == 40


def test_a_named_category_never_asks():
    r = run(categories=["Music"], unclear=["category"], when=when("this_weekend"))
    assert r.missing == () and r.filters.categories == ["Music"]


@pytest.mark.parametrize(
    "fields",
    [
        {"vibe": "Chilled"},  # "something chilled tonight"
        {"who": "Family with kids"},  # "things to do with the kids"
        {"vibe": "Lively", "who": "Friends"},  # "hen do, lively"
    ],
)
def test_a_vibe_or_a_group_is_enough_to_search_without_asking(fields):
    r = run(unclear=["category"], when=when("tonight"), **fields)
    assert r.missing == () and r.filters.categories is None


def test_a_vibe_doesnt_stop_other_questions():
    r = run(vibe="Chilled", unclear=["category", "date"], near_me=True)
    assert r.missing == ("location", "date")


def test_no_category_and_nothing_unclear_runs_with_no_category():
    r = run(when=when("this_weekend"))  # "just surprise me this weekend"
    assert r.missing == () and r.filters.categories is None


# Location (location rules) and radius ---------------------------------------------


def test_near_me_without_a_location_asks():
    r = run(near_me=True, when=when("tonight"))
    assert r.missing == ("location",) and r.filters.lat is None
    assert days(r) == ("2026-10-27T17:00:00+00:00", "2026-10-28T05:00:00+00:00")


def test_near_me_with_a_location_uses_it_at_one_mile():
    r = run(location=WATERLOO, near_me=True, when=when("tonight"))
    assert r.missing == () and r.used_location
    assert (r.filters.lat, r.filters.lon, r.filters.radius_km) == (51.5031, -0.1132, KM_PER_MILE)


def test_a_stated_distance_overrides_the_location_default():
    r = run(location=WATERLOO, distance={"value": 2, "unit": "km"})
    assert r.filters.radius_km == 2 and r.used_location


def test_a_bare_distance_without_a_place_asks_for_a_location():
    r = run(distance={"value": 1, "unit": "mi"})
    assert r.missing == ("location",) and r.filters.radius_km is None and r.notes == ()


def test_a_named_area_wins_over_the_users_point_and_never_asks():
    r = run(location=WATERLOO, near_me=True, place="Soho", when=when("this_weekend"))
    assert r.missing == () and not r.used_location
    assert (r.filters.lat, r.filters.lon, r.filters.radius_km) == (SOHO.lat, SOHO.lon, 1.0)


@pytest.mark.parametrize(
    ("place", "distance", "radius"),
    [
        ("Soho", None, 1.0),
        ("Camden", None, 6.0),
        ("Soho", {"value": 1, "unit": "mi"}, KM_PER_MILE),
        ("Soho", {"value": 3, "unit": "km"}, 3.0),
        ("Camden", {"value": 300, "unit": "km"}, 100.0),
    ],
    ids=["neighbourhood default", "borough default", "miles", "km", "capped at 100 km"],
)
def test_radius_is_the_area_default_unless_stated(place, distance, radius):
    r = run(place=place, distance=distance)
    assert r.filters.radius_km == pytest.approx(radius)


def test_an_unknown_place_is_a_note_not_a_question():
    r = run(place="Hampstead")
    assert r.missing == () and [n.code for n in r.notes] == ["unknown_place"]


def test_missing_reasons_come_in_the_fixed_order():
    r = run(near_me=True, unclear=["category", "budget", "date"], price_max=None, event="Pride")
    assert r.missing == ("location", "date", "budget", "category")
    assert MISSING_ORDER == ("location", "date", "budget", "price_basis", "event_date", "category")


# Questions --------------------------------------------------------------------------


def test_each_question_names_the_request_and_offers_its_choices():
    u = understood(keywords="Christmas markets", near_me=True, unclear=["date"])
    r = resolve(u, TUE_NOON, CITY)
    (loc, when_q) = questions(r.missing, u, CITY.name)
    assert (loc.reason, loc.choices) == ("location", LOCATION_CHOICES)
    assert loc.question == (
        "Christmas markets: which part of London, or should I use your location?"
    )
    assert when_q.question == "Christmas markets: which date do you mean?"
    assert when_q.choices == ()


def test_the_category_question_offers_categories_vibes_and_surprise_me():
    # "Something fun": no clear mapping, so both sets (round 2, decision 1).
    u = understood(keywords="fun", unclear=["category"])
    (q,) = questions(("category",), u, CITY.name)
    assert q.question == "Fun: what are you in the mood for?"
    assert [(g.field, g.choices) for g in q.groups] == [
        ("category", ("Music", "Comedy", "Food & Drink", "Arts", "Nightlife", "Family", "Sport")),
        ("vibe", ("Chilled", "Lively", "Cultural", "Foodie", "Outdoors")),
        (None, ("Just surprise me",)),
    ]
    assert q.choices == CATEGORY_CHOICES == tuple(c for g in q.groups for c in g.choices)


def test_other_questions_have_no_groups():
    u = understood(keywords="Christmas markets", near_me=True, price_max=60, price_scope="total")
    for q in questions(("location", "date", "price_basis"), u, CITY.name):
        assert q.groups == ()


def test_price_basis_and_event_date_questions():
    u = understood(keywords="christmas market", price_max=60, price_scope="total")
    (q,) = questions(("price_basis",), u, CITY.name)
    assert q.question == "Christmas market: is £60 per person or in total?"
    assert q.choices == ("Per person", "In total")
    u = understood(event="Pride in London")
    (q,) = questions(("event_date",), u, CITY.name)
    assert q.question == "I don't know the date for Pride in London. Can you give me a date?"


# Bank holidays ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("holiday", "expected"),
    [
        ("good_friday", ("2027-03-26T05:00:00+00:00", "2027-03-27T05:00:00+00:00")),
        ("easter_sunday", ("2027-03-28T05:00:00+01:00", "2027-03-29T05:00:00+01:00")),
        ("easter_monday", ("2027-03-29T05:00:00+01:00", "2027-03-30T05:00:00+01:00")),
        # Good Friday to Easter Monday, from the evening before; the clocks change on the
        # Sunday, so it ends at 05:00 BST.
        ("easter_weekend", ("2027-03-25T17:00:00+00:00", "2027-03-30T05:00:00+01:00")),
        ("early_may_bank_holiday", ("2027-05-03T05:00:00+01:00", "2027-05-04T05:00:00+01:00")),
        ("spring_bank_holiday", ("2027-05-31T05:00:00+01:00", "2027-06-01T05:00:00+01:00")),
        ("summer_bank_holiday", ("2027-08-30T05:00:00+01:00", "2027-08-31T05:00:00+01:00")),
        ("bank_holiday", ("2026-12-25T05:00:00+00:00", "2026-12-26T05:00:00+00:00")),
        # Christmas Day (Fri) to the Boxing Day substitute (Mon 28 Dec).
        ("bank_holiday_weekend", ("2026-12-24T17:00:00+00:00", "2026-12-29T05:00:00+00:00")),
    ],
)
def test_bank_holidays_resolve_from_the_snapshot(holiday, expected):
    r = run(when=when("holiday", holiday=holiday))
    assert days(r) == expected and r.missing == () and r.notes == ()


def test_a_bank_holiday_already_started_starts_now():
    now = datetime.fromisoformat("2027-03-29T14:00:00+01:00")
    r = run(now=now, when=when("holiday", holiday="easter_weekend"))
    assert days(r) == ("2027-03-29T14:00:00+01:00", "2027-03-30T05:00:00+01:00")


def test_a_holiday_beyond_the_snapshot_is_a_note(monkeypatch):
    monkeypatch.setattr(holidays, "bank_holidays", lambda: ((date(2026, 8, 31), "x"),))
    r = run(when=when("holiday", holiday="easter_monday"))
    assert r.filters.date_from is None and [n.code for n in r.notes] == ["no_such_date"]


def test_the_snapshot_is_govuk_england_and_wales_and_sorted():
    data = json.loads(holidays.SNAPSHOT.read_text())
    assert data["source"] == "https://www.gov.uk/bank-holidays.json"
    assert data["division"] == "england-and-wales"
    dates = [e["date"] for e in data["events"]]
    assert dates == sorted(dates) and len(dates) == len(set(dates))
    assert {"date": "2026-12-28", "title": "Boxing Day", "notes": "Substitute day"} in data[
        "events"
    ]


def test_bank_holidays_cover_six_months():
    """Fails when the snapshot runs out: refresh it with scripts/refresh_bank_holidays.py."""
    last = holidays.bank_holidays()[-1][0]
    assert (last - date.today()).days >= 183, f"bank_holidays.json ends {last}; refresh it"


def test_the_refresh_script_keeps_only_england_and_wales():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "refresh", REPO / "agent" / "scripts" / "refresh_bank_holidays.py"
    )
    refresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(refresh)
    feed = {
        "england-and-wales": {
            "division": "england-and-wales",
            "events": [
                {"title": "B", "date": "2027-01-01", "notes": "", "bunting": True},
                {"title": "A", "date": "2026-12-25", "notes": "", "bunting": True},
            ],
        },
        "scotland": {"division": "scotland", "events": [{"title": "S", "date": "2026-11-30"}]},
    }
    out = refresh.snapshot(feed, datetime(2026, 10, 27))
    assert out["fetched"] == "2026-10-27"
    assert out["events"] == [
        {"date": "2026-12-25", "title": "A", "notes": ""},
        {"date": "2027-01-01", "title": "B", "notes": ""},
    ]
