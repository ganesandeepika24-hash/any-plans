"""Code, not the model, turns intent into filters: dates, places, prices (principle 5)."""

from datetime import datetime

import pytest

from app.understand.resolve import date_window, normalise, resolve
from app.understand.schema import UnderstoodQuery, When
from tests.understand_support import london, reply, when

CITY = london()
TZ = CITY.tz
WED_EVENING = datetime.fromisoformat("2026-10-28T20:00:00+00:00")  # GMT since 25 Oct
SAT_2AM = datetime.fromisoformat("2026-10-31T02:00:00+00:00")
SUN_AFTERNOON = datetime.fromisoformat("2026-11-01T15:00:00+00:00")


def window(kind, now, **parts):
    w = date_window(When(**when(kind, **parts)), now, TZ)
    return None if w is None else tuple(d.isoformat() for d in w)


def understood(**fields) -> UnderstoodQuery:
    return UnderstoodQuery.model_validate_json(reply(**fields))


# Dates -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "parts", "expected"),
    [
        ("today", {}, ("2026-10-28T20:00:00+00:00", "2026-10-29T05:00:00+00:00")),
        ("tonight", {}, ("2026-10-28T20:00:00+00:00", "2026-10-29T05:00:00+00:00")),
        ("tomorrow", {}, ("2026-10-29T05:00:00+00:00", "2026-10-30T05:00:00+00:00")),
        ("this_weekend", {}, ("2026-10-30T17:00:00+00:00", "2026-11-02T05:00:00+00:00")),
        ("next_weekend", {}, ("2026-11-06T17:00:00+00:00", "2026-11-09T05:00:00+00:00")),
        ("this_week", {}, ("2026-10-28T20:00:00+00:00", "2026-11-02T05:00:00+00:00")),
        ("next_week", {}, ("2026-11-02T05:00:00+00:00", "2026-11-09T05:00:00+00:00")),
        (
            "weekday",
            {"weekday": "fri"},
            ("2026-10-30T05:00:00+00:00", "2026-10-31T05:00:00+00:00"),
        ),
        (
            "weekday",
            {"weekday": "wed"},
            ("2026-10-28T20:00:00+00:00", "2026-10-29T05:00:00+00:00"),
        ),
        (
            "weekday",
            {"weekday": "tue"},
            ("2026-11-03T05:00:00+00:00", "2026-11-04T05:00:00+00:00"),
        ),
        (
            "date",
            {"month": 11, "day": 14},
            ("2026-11-14T05:00:00+00:00", "2026-11-15T05:00:00+00:00"),
        ),
        (
            "date",
            {"month": 10, "day": 1},
            ("2027-10-01T05:00:00+01:00", "2027-10-02T05:00:00+01:00"),
        ),
    ],
    ids=[
        "today",
        "tonight",
        "tomorrow",
        "this weekend",
        "next weekend",
        "this week",
        "next week",
        "friday",
        "wednesday is today",
        "tuesday is next week",
        "14 november",
        "1 october has passed",
    ],
)
def test_windows_from_a_wednesday_evening(kind, parts, expected):
    assert window(kind, WED_EVENING, **parts) == expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("today", ("2026-10-31T02:00:00+00:00", "2026-10-31T05:00:00+00:00")),
        ("tonight", ("2026-10-31T02:00:00+00:00", "2026-10-31T05:00:00+00:00")),
        ("tomorrow", ("2026-10-31T05:00:00+00:00", "2026-11-01T05:00:00+00:00")),
        ("this_weekend", ("2026-10-31T02:00:00+00:00", "2026-11-02T05:00:00+00:00")),
    ],
)
def test_before_5am_counts_as_the_night_before(kind, expected):
    # 02:00 on Saturday is still Friday night: "tomorrow" is Saturday, not Sunday.
    assert window(kind, SAT_2AM) == expected


def test_this_weekend_from_inside_it_starts_now():
    assert window("this_weekend", SUN_AFTERNOON) == (
        "2026-11-01T15:00:00+00:00",
        "2026-11-02T05:00:00+00:00",
    )
    assert window("next_weekend", SUN_AFTERNOON) == (
        "2026-11-06T17:00:00+00:00",
        "2026-11-09T05:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (
            "2026-10-21T12:00:00+01:00",  # clocks go back on Sunday 25 October
            ("2026-10-23T17:00:00+01:00", "2026-10-26T05:00:00+00:00"),
        ),
        (
            "2026-03-25T12:00:00+00:00",  # clocks go forward on Sunday 29 March
            ("2026-03-27T17:00:00+00:00", "2026-03-30T05:00:00+01:00"),
        ),
    ],
    ids=["autumn", "spring"],
)
def test_weekends_across_a_clock_change_keep_local_times(now, expected):
    assert window("this_weekend", datetime.fromisoformat(now)) == expected


@pytest.mark.parametrize(("month", "day"), [(2, 29), (11, 31), (2, 30)])
def test_dates_that_do_not_exist_in_the_coming_year_resolve_to_nothing(month, day):
    assert window("date", WED_EVENING, month=month, day=day) is None


def test_29_february_resolves_in_a_leap_year():
    assert window("date", datetime.fromisoformat("2027-12-01T12:00:00+00:00"), month=2, day=29) == (
        "2028-02-29T05:00:00+00:00",
        "2028-03-01T05:00:00+00:00",
    )


def test_an_impossible_date_drops_dates_and_says_so():
    r = resolve(
        understood(when=when("date", month=2, day=30), categories=["Music"]), WED_EVENING, CITY
    )
    assert r.filters.date_from is None and r.filters.categories == ["Music"]
    assert [n.code for n in r.notes] == ["no_such_date"]


# Places ----------------------------------------------------------------------------


def test_area_names_and_aliases_are_unambiguous():
    # Each normalised name or alias belongs to one area ("The Angel" and "Angel" may
    # both point at Angel).
    owners: dict[str, set[str]] = {}
    for area in CITY.areas:
        for name in (area.name, *area.aliases):
            owners.setdefault(normalise(name), set()).add(area.id)
    assert {name: ids for name, ids in owners.items() if len(ids) > 1} == {}


@pytest.mark.parametrize(
    ("place", "area_id"),
    [
        ("Soho", "london-soho"),
        ("soho", "london-soho"),
        ("King's Cross", "london-kings-cross"),
        ("Kings Cross", "london-kings-cross"),
        ("kings cross", "london-kings-cross"),
        ("The Angel", "london-angel"),
        ("Angel", "london-angel"),
        ("the City", "london-city-of-london"),
        ("Square Mile", "london-city-of-london"),
        ("Greenwich", "london-greenwich"),
        ("Greenwich town centre", "london-greenwich-town-centre"),
        ("Barking & Dagenham", "london-barking-and-dagenham"),
    ],
)
def test_places_match_names_and_aliases(place, area_id):
    area = CITY.find_area(place)
    assert area is not None and area.id == area_id


def test_seed_parsing_matches_the_database_shape():
    assert len(CITY.areas) == 53  # understand_context returns 53 in CI's db
    assert all(alias for a in CITY.areas for alias in a.aliases)


@pytest.mark.parametrize("place", ["!!!", "the", "  "])
def test_a_place_with_no_letters_matches_nothing(place):
    assert CITY.find_area(place) is None


def test_a_place_uses_its_centre_and_default_radius():
    r = resolve(understood(place="Soho"), WED_EVENING, CITY)
    soho = CITY.find_area("Soho")
    assert (r.filters.lat, r.filters.lon, r.filters.radius_km) == (soho.lat, soho.lon, 1.0)
    assert r.notes == ()


def test_a_borough_gets_the_wider_default():
    assert resolve(understood(place="Bromley"), WED_EVENING, CITY).filters.radius_km == 6.0


def test_a_distance_in_miles_is_converted_in_code():
    r = resolve(understood(place="Soho", distance={"value": 2, "unit": "mi"}), WED_EVENING, CITY)
    assert r.filters.radius_km == pytest.approx(3.218688)


def test_a_huge_distance_is_capped_with_a_note():
    r = resolve(understood(place="Soho", distance={"value": 300, "unit": "km"}), WED_EVENING, CITY)
    assert r.filters.radius_km == 100
    assert [n.code for n in r.notes] == ["radius_capped"]


def test_an_unknown_place_drops_location_and_says_so():
    r = resolve(understood(place="Narnia", categories=["Arts"]), WED_EVENING, CITY)
    assert r.filters.lat is None and r.filters.categories == ["Arts"]
    assert [n.code for n in r.notes] == ["unknown_place"]
    assert "Narnia" in r.notes[0].message


@pytest.mark.parametrize("place", ["Green", "King", "Soh", "Camden Tow", "Cross"])
def test_part_of_a_name_is_not_a_match(place):
    # No guessing: "Green" isn't Greenwich.
    r = resolve(understood(place=place), WED_EVENING, CITY)
    assert r.filters.lat is None
    assert [n.code for n in r.notes] == ["unknown_place"]


def test_an_unknown_place_with_a_distance_gets_one_note():
    r = resolve(understood(place="Narnia", distance={"value": 1, "unit": "km"}), WED_EVENING, CITY)
    assert [n.code for n in r.notes] == ["unknown_place"]


def test_a_distance_without_a_place_is_dropped_with_a_note():
    r = resolve(understood(distance={"value": 1, "unit": "km"}), WED_EVENING, CITY)
    assert r.filters.radius_km is None
    assert [n.code for n in r.notes] == ["distance_without_place"]


def test_london_itself_means_no_location_filter():
    r = resolve(understood(place="London"), WED_EVENING, CITY)
    assert r.filters.lat is None and r.notes == ()


# Prices, categories, keywords -------------------------------------------------------


def test_free_means_a_known_price_of_zero():
    f = resolve(understood(free=True, price_max=50), WED_EVENING, CITY).filters
    assert (f.price_max, f.include_unpriced, f.price_min) == (0, False, None)


def test_prices_pass_through_and_unpriced_stays_at_the_default():
    f = resolve(understood(price_min=10, price_max=25), WED_EVENING, CITY).filters
    assert (f.price_min, f.price_max, f.include_unpriced) == (10, 25, None)


def test_an_inverted_price_range_is_dropped_with_a_note():
    r = resolve(understood(price_min=30, price_max=10), WED_EVENING, CITY)
    assert (r.filters.price_min, r.filters.price_max) == (None, None)
    assert [n.code for n in r.notes] == ["price_range_inverted"]


def test_keywords_become_the_query_text_and_nothing_said_means_no_filters():
    r = resolve(understood(keywords="drag brunch"), WED_EVENING, CITY)
    assert r.query_text == "drag brunch"
    assert r.filters.to_json() == {}


def test_a_full_query():
    r = resolve(
        understood(
            keywords="jazz",
            categories=["Music"],
            when=when("tonight"),
            place="Soho",
            price_max=20,
        ),
        WED_EVENING,
        CITY,
    )
    soho = CITY.find_area("Soho")
    assert r.filters.to_json() == {
        "date_from": "2026-10-28T20:00:00+00:00",
        "date_to": "2026-10-29T05:00:00+00:00",
        "categories": ["Music"],
        "price_max": 20.0,
        "lat": soho.lat,
        "lon": soho.lon,
        "radius_km": 1.0,
    }
    assert r.query_text == "jazz" and r.notes == ()
