"""Code, not the model, turns intent into filters: dates, places, prices (principle 5)."""

from datetime import datetime
from itertools import pairwise

import pytest

from app.understand.resolve import NO_RANGES, DateRanges, date_window, normalise, resolve
from app.understand.schema import SearchFilters, UnderstoodQuery, When
from tests.understand_support import london, migration_config, reply, when

CITY = london()
TZ = CITY.tz
RANGES = DateRanges.model_validate(migration_config()["date_ranges"])
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


# Calendar (decision 0019) ----------------------------------------------------------


def event_window(event, now=WED_EVENING, **fields):
    r = resolve(understood(event=event, **fields), now, CITY)
    f = r.filters
    got = None if f.date_from is None else (f.date_from.isoformat(), f.date_to.isoformat())
    return got, r.missing, [n.code for n in r.notes]


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("Remembrance Sunday", ("2026-11-08T05:00:00+00:00", "2026-11-09T05:00:00+00:00")),
        ("Hanukkah", ("2026-12-04T05:00:00+00:00", "2026-12-13T05:00:00+00:00")),
        ("Chanukah", ("2026-12-04T05:00:00+00:00", "2026-12-13T05:00:00+00:00")),
        ("mothering sunday", ("2027-03-07T05:00:00+00:00", "2027-03-08T05:00:00+00:00")),
        ("Feb half term", ("2027-02-15T05:00:00+00:00", "2027-02-20T05:00:00+00:00")),
        ("Vaisakhi", ("2027-04-14T05:00:00+01:00", "2027-04-15T05:00:00+01:00")),
        ("Chelsea Flower Show", ("2027-05-18T05:00:00+01:00", "2027-05-23T05:00:00+01:00")),
    ],
)
def test_a_named_event_takes_its_whole_days_from_the_calendar(event, expected):
    assert event_window(event) == (expected, (), [])


def test_an_estimated_date_is_used_and_says_so():
    # "Eid" is whichever Eid comes next: Eid al-Fitr, 9 or 10 March depending on the moon.
    assert event_window("Eid") == (
        ("2027-03-09T05:00:00+00:00", "2027-03-11T05:00:00+00:00"),
        (),
        ["estimated_date"],
    )


def test_a_bare_half_term_is_the_one_now_or_the_next():
    # During October half-term it's the rest of this one; after it, February's.
    assert event_window("half-term")[0] == (
        "2026-10-28T20:00:00+00:00",
        "2026-10-31T05:00:00+00:00",
    )
    after = datetime.fromisoformat("2026-11-02T12:00:00+00:00")
    assert event_window("half-term", after)[0] == (
        "2027-02-15T05:00:00+00:00",
        "2027-02-20T05:00:00+00:00",
    )


def test_an_event_still_counts_until_5am_the_next_day():
    late = datetime.fromisoformat("2026-11-09T02:00:00+00:00")
    assert event_window("Remembrance Sunday", late)[0] == (
        "2026-11-09T02:00:00+00:00",
        "2026-11-09T05:00:00+00:00",
    )


def test_a_weekday_with_an_event_is_that_day_of_it():
    # "Diwali on Sunday": the weekday agrees with the calendar's day (festival doc).
    assert event_window("Diwali", when=when("weekday", "sun"))[0] == (
        "2026-11-08T05:00:00+00:00",
        "2026-11-09T05:00:00+00:00",
    )
    assert event_window("Hanukkah", when=when("weekday", "wed"))[0] == (
        "2026-12-09T05:00:00+00:00",
        "2026-12-10T05:00:00+00:00",
    )


def test_a_weekday_in_an_event_under_way_is_one_still_to_come():
    # Wednesday evening of October half-term: Thursday is to come, Tuesday has gone.
    assert event_window("half-term", when=when("weekday", "thu"))[0] == (
        "2026-10-29T05:00:00+00:00",
        "2026-10-30T05:00:00+00:00",
    )
    assert event_window("half-term", when=when("weekday", "tue")) == (None, ("date",), [])


@pytest.mark.parametrize(
    ("event", "weekday"),
    [("Diwali", "sat"), ("Hanukkah", "sat")],  # not one of its days; two of its days
)
def test_a_weekday_that_does_not_pick_one_day_of_the_event_asks_for_a_date(event, weekday):
    assert event_window(event, when=when("weekday", weekday)) == (None, ("date",), [])


@pytest.mark.parametrize(
    ("event", "fields"),
    [
        ("Pride in London", {}),
        ("Notting Hill Carnival", {"when": when("weekday", "sun")}),  # row 57
        ("Diwal", {}),  # part of a name is no match
    ],
)
def test_an_event_the_calendar_does_not_have_asks_for_its_date(event, fields):
    assert event_window(event, **fields) == (None, ("event_date",), [])


def test_an_event_that_has_ended_asks_for_its_date():
    # Diwali 2026 is over and 2027's isn't in the calendar yet.
    after = datetime.fromisoformat("2026-11-10T12:00:00+00:00")
    assert event_window("Diwali", after) == (None, ("event_date",), [])


def test_a_date_given_with_an_event_wins_over_the_calendar():
    assert event_window("Diwali", when=when("date", month=11, day=7))[0] == (
        "2026-11-07T05:00:00+00:00",
        "2026-11-08T05:00:00+00:00",
    )


def test_occurrences_that_share_a_name_never_overlap():
    # "half-term" names three entries; the next one must be a single answer.
    by_name: dict[str, list] = {}
    for d in CITY.calendar:
        for name in {normalise(n) for n in (d.name, *d.aliases)}:
            by_name.setdefault(name, []).append(d)
    for name, ds in by_name.items():
        ds.sort(key=lambda d: d.first)
        assert all(a.last < b.first for a, b in pairwise(ds)), name


def test_calendar_seed_parsing_matches_the_database_shape():
    assert len(CITY.calendar) == 12
    assert all(d.first <= d.last for d in CITY.calendar)
    assert [d.id for d in CITY.calendar if d.estimate] == ["london-2027-eid-al-fitr"]


# Spans (decision 0020) -------------------------------------------------------------


def iso(d):
    return d.isoformat() if d else None


def spans(w, now=WED_EVENING, ranges=RANGES):
    """The resolved dates as (date_from, date_to, windows, missing, choices, notes)."""
    r = resolve(understood(when=w), now, CITY, ranges=ranges)
    f = r.filters
    windows = [(iso(a), iso(b)) for a, b in f.windows] if f.windows else None
    return (
        iso(f.date_from),
        iso(f.date_to),
        windows,
        r.missing,
        r.date_choices,
        [n.code for n in r.notes],
    )


def dates(w, now=WED_EVENING, ranges=RANGES):
    return spans(w, now, ranges)[:2]


@pytest.mark.parametrize(
    ("w", "now", "expected"),
    [
        # A later month this year; across the clock change at the end of March.
        (
            when("month", month=3),
            WED_EVENING,
            ("2027-03-01T05:00:00+00:00", "2027-04-01T05:00:00+01:00"),
        ),
        # This month: from now.
        (
            when("month", month=10),
            WED_EVENING,
            ("2026-10-28T20:00:00+00:00", "2026-11-01T05:00:00+00:00"),
        ),
        # A month that's over this year is next year's.
        (
            when("month", month=9),
            WED_EVENING,
            ("2027-09-01T05:00:00+01:00", "2027-10-01T05:00:00+01:00"),
        ),
        # December runs into January.
        (
            when("month", month=12),
            WED_EVENING,
            ("2026-12-01T05:00:00+00:00", "2027-01-01T05:00:00+00:00"),
        ),
        (
            when("this_month"),
            WED_EVENING,
            ("2026-10-28T20:00:00+00:00", "2026-11-01T05:00:00+00:00"),
        ),
        (
            when("next_month"),
            WED_EVENING,
            ("2026-11-01T05:00:00+00:00", "2026-12-01T05:00:00+00:00"),
        ),
        (
            when("next_month"),
            datetime.fromisoformat("2026-12-10T12:00:00+00:00"),
            ("2027-01-01T05:00:00+00:00", "2027-02-01T05:00:00+00:00"),
        ),
    ],
)
def test_a_month_is_the_whole_month_from_now(w, now, expected):
    assert dates(w, now) == expected


@pytest.mark.parametrize(
    ("w", "expected"),
    [
        # Row 68: both days included.
        (
            when("range", month=11, day=14, end_month=11, end_day=15),
            ("2026-11-14T05:00:00+00:00", "2026-11-16T05:00:00+00:00"),
        ),
        # Across the new year.
        (
            when("range", month=12, day=28, end_month=1, end_day=2),
            ("2026-12-28T05:00:00+00:00", "2027-01-03T05:00:00+00:00"),
        ),
        # Under way: from now to the end.
        (
            when("range", month=10, day=26, end_month=10, end_day=30),
            ("2026-10-28T20:00:00+00:00", "2026-10-31T05:00:00+00:00"),
        ),
    ],
)
def test_a_range_includes_both_days(w, expected):
    assert dates(w) == expected


def test_a_range_longer_than_config_allows_asks():
    w = when("range", month=11, day=1, end_month=1, end_day=31)  # 92 days, over 62
    assert spans(w)[2:4] == (None, ("date",))


def test_a_range_ending_on_a_day_that_does_not_exist_is_dropped_with_a_note():
    w = when("range", month=2, day=1, end_month=2, end_day=30)
    assert spans(w)[:2] == (None, None) and spans(w)[5] == ["no_such_date"]


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # Row 36, from before winter.
        (WED_EVENING, ("2026-11-01T05:00:00+00:00", "2027-02-01T05:00:00+00:00")),
        # From inside it.
        (
            datetime.fromisoformat("2026-12-10T12:00:00+00:00"),
            ("2026-12-10T12:00:00+00:00", "2027-02-01T05:00:00+00:00"),
        ),
        # After it: next winter.
        (
            datetime.fromisoformat("2027-02-05T12:00:00+00:00"),
            ("2027-11-01T05:00:00+00:00", "2028-02-01T05:00:00+00:00"),
        ),
    ],
)
def test_winter_comes_from_config(now, expected):
    assert dates(when("season", season="winter"), now) == expected


@pytest.mark.parametrize(("season", "ranges"), [("summer", RANGES), ("winter", NO_RANGES)])
def test_a_season_with_no_dates_in_config_asks(season, ranges):
    assert spans(when("season", season=season), ranges=ranges)[:4] == (None, None, None, ("date",))


@pytest.mark.parametrize(
    ("w", "choices"),
    [
        (when("every_weekday", "sat"), ("Next 4 Saturdays", "This month", "Pick a month")),
        (when("every_weekday", "fri"), ("Next 4 Fridays", "This month", "Pick a month")),
        (when("every_weekend"), ("Next 4 weekends", "This month", "Pick a month")),
    ],
)
def test_a_recurring_day_with_no_count_or_month_asks_with_choices(w, choices):
    assert spans(w)[:5] == (None, None, None, ("date",), choices)


def test_the_choice_count_comes_from_config():
    three = RANGES.model_copy(update={"choice_count": 3})
    assert spans(when("every_weekend"), ranges=three)[4][0] == "Next 3 weekends"


def test_the_next_4_saturdays_are_four_windows_in_one_span():
    assert spans(when("every_weekday", "sat", count=4)) == (
        "2026-10-31T05:00:00+00:00",
        "2026-11-22T05:00:00+00:00",
        [
            ("2026-10-31T05:00:00+00:00", "2026-11-01T05:00:00+00:00"),
            ("2026-11-07T05:00:00+00:00", "2026-11-08T05:00:00+00:00"),
            ("2026-11-14T05:00:00+00:00", "2026-11-15T05:00:00+00:00"),
            ("2026-11-21T05:00:00+00:00", "2026-11-22T05:00:00+00:00"),
        ],
        (),
        (),
        [],
    )


def test_on_a_saturday_the_first_of_the_next_saturdays_is_today_from_now():
    sat_afternoon = datetime.fromisoformat("2026-10-31T15:00:00+00:00")
    windows = spans(when("every_weekday", "sat", count=2), sat_afternoon)[2]
    assert windows == [
        ("2026-10-31T15:00:00+00:00", "2026-11-01T05:00:00+00:00"),
        ("2026-11-07T05:00:00+00:00", "2026-11-08T05:00:00+00:00"),
    ]
    # At 02:00 on Saturday it's still Friday night, so Saturday is still to come.
    assert spans(when("every_weekday", "sat", count=1), SAT_2AM)[:2] == (
        "2026-10-31T05:00:00+00:00",
        "2026-11-01T05:00:00+00:00",
    )


def test_weekends_run_from_friday_evening_and_include_the_one_under_way():
    assert spans(when("every_weekend", count=2))[2] == [
        ("2026-10-30T17:00:00+00:00", "2026-11-02T05:00:00+00:00"),
        ("2026-11-06T17:00:00+00:00", "2026-11-09T05:00:00+00:00"),
    ]
    assert spans(when("every_weekend", count=1), SUN_AFTERNOON)[:2] == (
        "2026-11-01T15:00:00+00:00",
        "2026-11-02T05:00:00+00:00",
    )


def test_saturdays_this_month_next_month_and_in_a_named_month():
    # One Saturday left in October: a single window, so no windows list.
    assert spans(when("every_weekday", "sat", within="this_month"))[:3] == (
        "2026-10-31T05:00:00+00:00",
        "2026-11-01T05:00:00+00:00",
        None,
    )
    assert [a[:10] for a, _ in spans(when("every_weekday", "sat", within="next_month"))[2]] == [
        "2026-11-07",
        "2026-11-14",
        "2026-11-21",
        "2026-11-28",
    ]
    assert [a[:10] for a, _ in spans(when("every_weekday", "sat", month=3))[2]] == [
        "2027-03-06",
        "2027-03-13",
        "2027-03-20",
        "2027-03-27",
    ]


def test_no_saturdays_left_this_month_asks_again_rather_than_dropping_dates():
    last_sunday = datetime.fromisoformat("2027-01-31T12:00:00+00:00")
    assert spans(when("every_weekday", "sat", within="this_month"), last_sunday)[:5] == (
        None,
        None,
        None,
        ("date",),
        ("Next 4 Saturdays", "This month", "Pick a month"),
    )


def test_too_many_recurring_days_stop_at_the_config_limit_with_a_note():
    got = spans(when("every_weekday", "sat", count=12))
    assert len(got[2]) == RANGES.max_count and got[5] == ["count_capped"]


def test_date_window_refuses_a_recurring_day():
    with pytest.raises(ValueError, match="date_windows"):
        date_window(When(**when("every_weekday", "sat", count=2)), WED_EVENING, TZ)


@pytest.mark.parametrize(
    "windows",
    [
        [("2026-11-07T05:00:00+00:00", "2026-11-08T05:00:00+00:00")],  # only one
        [  # out of order
            ("2026-11-07T05:00:00+00:00", "2026-11-08T05:00:00+00:00"),
            ("2026-10-31T05:00:00+00:00", "2026-11-01T05:00:00+00:00"),
        ],
        [  # outside date_from to date_to
            ("2026-10-31T05:00:00+00:00", "2026-11-01T05:00:00+00:00"),
            ("2026-12-05T05:00:00+00:00", "2026-12-06T05:00:00+00:00"),
        ],
    ],
)
def test_windows_must_be_several_in_order_and_inside_the_span(windows):
    with pytest.raises(ValueError):
        SearchFilters(
            date_from="2026-10-31T05:00:00+00:00",
            date_to="2026-11-22T05:00:00+00:00",
            windows=windows,
        )


def test_windows_round_trip_through_json():
    f = resolve(understood(when=when("every_weekday", "sat", count=2)), WED_EVENING, CITY).filters
    assert SearchFilters(**f.to_json()) == f
    assert f.to_json()["windows"][0] == ["2026-10-31T05:00:00+00:00", "2026-11-01T05:00:00+00:00"]


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


def test_a_distance_without_a_place_asks_for_a_location():
    r = resolve(understood(distance={"value": 1, "unit": "km"}), WED_EVENING, CITY)
    assert r.filters.radius_km is None and r.notes == ()
    assert r.missing == ("location",)


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
