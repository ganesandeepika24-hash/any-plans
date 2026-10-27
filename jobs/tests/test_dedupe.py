"""Cross-source dedupe (decision 0009, migration 007).

The Skiddle events come from the real fixture (dry run 40386725066). Every Ticketmaster
event here is HAND-WRITTEN: there is no real Ticketmaster sample yet. Recalibrate these
tests and TITLE_THRESHOLD after the first Ticketmaster dry run.
"""

import copy
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest

from jobs import dedupe, ingest
from jobs.db import Database
from jobs.dedupe import Pair, Record, choose_links, title_score, titles_match
from tests.conftest import NOW, fixture

M_PER_DEG_LAT = 111_257  # metres per degree of latitude at London's latitude
MINUTE = timedelta(minutes=1)


# titles_match (no database) -------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [
        ("Jazz", "Jazz Night", False),  # one shared token, and not both a single identical one
        ("Hamilton", "Hamilton", True),
        ("Jazz Night", "Jazz Night Live", True),  # "live" is filler
        ("Harry Potter And The Cursed Child", "Harry Potter and the Cursed Child", True),
        ("EFG London Jazz Festival", "EFG London Jazz Festival 2026", True),
        ("Café Tacuba", "Cafe Tacuba", True),  # accents stripped
        ("Rock & Roll Bingo", "Rock and Roll Bingo", True),  # & becomes and
        ("King's Cross Social", "Kings Cross Social", True),  # apostrophes dropped
        ("The Live London", "Tickets at London", False),  # nothing left after filler
        ("Comedy Night", "Jazz Night", False),
        ("Hamilton", "Hamilton Sing-Along", False),
    ],
)
def test_titles_match(x, y, expected):
    assert titles_match(x, y) is expected


def test_the_score_is_the_better_of_overlap_and_sequence_ratio():
    assert title_score("Sheer Terror", "Sheer Terror") == 1.0
    assert title_score("Jazz Night", "Jazz Night at Ronnie Scotts") == 1.0  # overlap of the smaller


# One-to-one and the kept record (no database) --------------------------------------


def rec(source, source_id, filled, start=NOW):
    return Record(uuid.uuid4(), source, source_id, "Same Show", start, "active", "london", filled)


def test_one_to_one_takes_the_closest_time_first():
    tm = rec("ticketmaster", "tm-1", 5)
    near, far = rec("skiddle", "sk-near", 5), rec("skiddle", "sk-far", 5, NOW + 5 * MINUTE)
    links = choose_links([Pair(tm, far, 10.0, 1.0), Pair(tm, near, 10.0, 1.0)])
    assert [(k.source_id, d.source_id) for k, d, _ in links] == [("tm-1", "sk-near")]


def test_the_richer_record_is_kept_whatever_its_source():
    tm, sk = rec("ticketmaster", "tm-1", 3), rec("skiddle", "sk-1", 6)
    [(kept, duplicate, _)] = choose_links([Pair(tm, sk, 10.0, 1.0)])
    assert (kept.source, duplicate.source) == ("skiddle", "ticketmaster")


def test_ticketmaster_wins_a_tie():
    tm, sk = rec("ticketmaster", "tm-1", 5), rec("skiddle", "sk-1", 5)
    [(kept, _, _)] = choose_links([Pair(sk, tm, 10.0, 1.0)])
    assert kept.source == "ticketmaster"


# Against the database ---------------------------------------------------------------

SKIDDLE = {str(e["id"]): e for e in fixture("skiddle/events-page.json")["results"]}


def add(
    sql, source, source_id, title, start_at, lat, lon, *, city="london", status="active", **fields
):
    columns = [
        "city_id",
        "source",
        "source_id",
        "title",
        "category",
        "start_at",
        "status",
        "geom",
        *fields,
    ]
    values = [
        city,
        source,
        source_id,
        title,
        "Music",
        start_at,
        status,
        f"SRID=4326;POINT({lon} {lat})",
        *fields.values(),
    ]
    placeholders = ", ".join(["%s"] * len(values))
    return sql(
        f"insert into events ({', '.join(columns)}) values ({placeholders}) returning id",  # noqa: S608
        values,
    )[0][0]


def north(lat, metres):
    return lat + metres / M_PER_DEG_LAT


def duplicate_of(sql, source_id):
    return sql("select duplicate_of from events where source_id = %s", (source_id,))[0][0]


def visible_to_anon(db_url):
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("set role anon")
        rows = conn.execute("select source_id from events order by 1").fetchall()
    return [r[0] for r in rows]


LAT, LON = 51.5131668, -0.1297073  # Palace Theatre, from the real Skiddle event 42504394
START = datetime(2026, 11, 3, 19, tzinfo=UTC)


@pytest.fixture
def pair(sql):
    """A matching pair at the Palace Theatre: Skiddle (real title) and Ticketmaster
    (HAND-WRITTEN) 30 m away at the same time."""
    add(sql, "skiddle", "42504394", "Harry Potter And The Cursed Child", START, LAT, LON)
    add(
        sql,
        "ticketmaster",
        "tm-hp",
        "Harry Potter and the Cursed Child",
        START,
        north(LAT, 30),
        LON,
        url="https://example.com/tm-hp",
    )


def test_a_matching_pair_is_linked(db, sql, pair):
    summary = dedupe.run(db, NOW, dry_run=False)
    assert duplicate_of(sql, "42504394") is not None  # the Ticketmaster record is richer
    assert duplicate_of(sql, "tm-hp") is None
    [city] = summary.cities
    assert (city.city, city.counts.fetched, city.counts.new, city.counts.updated) == (
        "london",
        1,
        1,
        0,
    )
    assert city.links[0]["kept"] == "ticketmaster:tm-hp"


@pytest.mark.parametrize(
    "change",
    [
        "different_dates",
        "different_cities",
        "gap_31_minutes",
        "distance_101_m",
        "dissimilar_titles",
        "same_source",
        "different_statuses",
    ],
)
def test_non_matches(db, sql, change, request):
    sk_start, tm_start, tm_lat = START, START, north(LAT, 30)
    title, tm_source, tm_status, tm_city = (
        "Harry Potter and the Cursed Child",
        "ticketmaster",
        "active",
        "london",
    )
    if change == "different_dates":  # 23:50 against 00:10 London time (GMT in November)
        sk_start = datetime(2026, 11, 3, 23, 50, tzinfo=UTC)
        tm_start = datetime(2026, 11, 4, 0, 10, tzinfo=UTC)
    elif change == "different_cities":
        sql(
            "insert into cities (id, name, timezone, active) values"
            " ('manchester', 'Manchester', 'Europe/London', false) on conflict do nothing"
        )
        request.addfinalizer(
            lambda: sql(
                "delete from events where city_id = 'manchester';"
                " delete from job_runs where city_id = 'manchester';"
                " delete from cities where id = 'manchester'"
            )
        )
        tm_city = "manchester"
    elif change == "gap_31_minutes":
        tm_start = START + 31 * MINUTE
    elif change == "distance_101_m":
        tm_lat = north(LAT, 101)
    elif change == "dissimilar_titles":
        title = "Phantom of the Opera"
    elif change == "same_source":
        tm_source = "skiddle"
    elif change == "different_statuses":
        tm_status = "cancelled"

    add(sql, "skiddle", "42504394", "Harry Potter And The Cursed Child", sk_start, LAT, LON)
    add(sql, tm_source, "tm-hp", title, tm_start, tm_lat, LON, city=tm_city, status=tm_status)
    if change == "distance_101_m":
        [(metres,)] = sql(
            "select extensions.st_distance(a.geom, b.geom) from events a, events b"
            " where a.source_id = '42504394' and b.source_id = 'tm-hp'"
        )
        assert 100 < metres <= 102  # measured on geography, in metres

    dedupe.run(db, NOW, dry_run=False)
    assert sql("select count(*) from events where duplicate_of is not null") == [(0,)]


def test_99_m_still_matches(db, sql):
    add(sql, "skiddle", "42504394", "Harry Potter And The Cursed Child", START, LAT, LON)
    add(
        sql,
        "ticketmaster",
        "tm-hp",
        "Harry Potter and the Cursed Child",
        START,
        north(LAT, 99),
        LON,
    )
    dedupe.run(db, NOW, dry_run=False)
    assert sql("select count(*) from events where duplicate_of is not null") == [(1,)]


def test_one_to_one_in_the_database(db, sql):
    add(sql, "ticketmaster", "tm-hp", "Harry Potter and the Cursed Child", START, LAT, LON)
    add(sql, "skiddle", "sk-a", "Harry Potter And The Cursed Child", START, north(LAT, 10), LON)
    add(
        sql,
        "skiddle",
        "sk-b",
        "Harry Potter And The Cursed Child",
        START + 5 * MINUTE,
        north(LAT, 10),
        LON,
    )
    dedupe.run(db, NOW, dry_run=False)
    linked = sql("select source_id from events where duplicate_of is not null order by 1")
    assert linked == [("sk-a",)]  # the nearer start time; sk-b stays visible


def test_the_richer_record_is_kept(db, sql):
    add(sql, "ticketmaster", "tm-hp", "Harry Potter and the Cursed Child", START, LAT, LON)
    add(
        sql,
        "skiddle",
        "42504394",
        "Harry Potter And The Cursed Child",
        START,
        north(LAT, 30),
        LON,
        price_min=30,
        price_max=210,
        currency="GBP",
        url="https://www.skiddle.com/x",
    )
    dedupe.run(db, NOW, dry_run=False)
    assert duplicate_of(sql, "tm-hp") is not None
    assert duplicate_of(sql, "42504394") is None


def test_ticketmaster_wins_a_tie_in_the_database(db, sql):
    add(sql, "skiddle", "42504394", "Harry Potter And The Cursed Child", START, LAT, LON)
    add(
        sql,
        "ticketmaster",
        "tm-hp",
        "Harry Potter and the Cursed Child",
        START,
        north(LAT, 30),
        LON,
    )
    dedupe.run(db, NOW, dry_run=False)
    assert duplicate_of(sql, "42504394") is not None


def test_reruns_give_the_same_result(db, sql, pair):
    dedupe.run(db, NOW, dry_run=False)
    first = sql("select source_id, duplicate_of from events order by 1")
    second = dedupe.run(db, NOW, dry_run=False)
    assert sql("select source_id, duplicate_of from events order by 1") == first
    assert (second.cities[0].counts.new, second.cities[0].counts.updated) == (0, 0)


def test_a_stale_link_is_cleared(db, sql, pair):
    other = add(
        sql, "skiddle", "unrelated", "Something Else Entirely", START + 3 * 60 * MINUTE, LAT, LON
    )
    sql(
        "update events set duplicate_of = (select id from events where source_id = 'tm-hp')"
        " where id = %s",
        (other,),
    )
    summary = dedupe.run(db, NOW, dry_run=False)
    assert duplicate_of(sql, "unrelated") is None
    assert summary.cities[0].counts.updated == 1


def test_anon_cannot_see_duplicates(db, db_url, sql):
    start = NOW + 48 * 60 * MINUTE  # current, so the public policy would otherwise show both
    add(sql, "skiddle", "42504394", "Harry Potter And The Cursed Child", start, LAT, LON)
    add(
        sql,
        "ticketmaster",
        "tm-hp",
        "Harry Potter and the Cursed Child",
        start,
        north(LAT, 30),
        LON,
        url="https://example.com/tm-hp",
    )
    assert visible_to_anon(db_url) == ["42504394", "tm-hp"]
    dedupe.run(db, NOW, dry_run=False)
    assert visible_to_anon(db_url) == ["tm-hp"]


def test_deleting_the_kept_record_shows_the_other_again(db, db_url, sql):
    start = NOW + 48 * 60 * MINUTE
    add(sql, "skiddle", "42504394", "Harry Potter And The Cursed Child", start, LAT, LON)
    add(
        sql,
        "ticketmaster",
        "tm-hp",
        "Harry Potter and the Cursed Child",
        start,
        north(LAT, 30),
        LON,
        url="https://example.com/tm-hp",
    )
    dedupe.run(db, NOW, dry_run=False)
    sql("delete from events where source_id = 'tm-hp'")
    assert duplicate_of(sql, "42504394") is None  # on delete set null
    assert visible_to_anon(db_url) == ["42504394"]


def test_job_runs_row_per_city(db, sql, pair):
    dedupe.run(db, NOW, dry_run=False)
    assert sql(
        "select job, city_id, fetched, new, updated, failed, deleted,"
        " duration_ms is not null from job_runs"
    ) == [("dedupe", "london", 1, 1, 0, 0, 0, True)]


def test_a_crashed_run_keeps_duration_null_and_changes_nothing(db, sql, pair, monkeypatch):
    def fail(*args):
        raise RuntimeError("write failed")

    monkeypatch.setattr(db, "set_duplicates", fail)
    with pytest.raises(RuntimeError):
        dedupe.run(db, NOW, dry_run=False)
    assert sql("select job, duration_ms from job_runs") == [("dedupe", None)]
    assert sql("select count(*) from events where duplicate_of is not null") == [(0,)]


def test_dry_run_prints_the_pairs_and_writes_nothing(db_url, db, sql, pair):
    read_only = Database.connect(db_url, read_only=True)
    try:
        summary = dedupe.run(read_only, NOW, dry_run=True)
    finally:
        read_only.close()
    [city] = summary.cities
    assert city.links == [
        {
            "kept": "ticketmaster:tm-hp",
            "duplicate": "skiddle:42504394",
            "kept_title": "Harry Potter and the Cursed Child",
            "duplicate_title": "Harry Potter And The Cursed Child",
            "score": 1.0,
            "gap_minutes": 0.0,
            "metres": city.links[0]["metres"],
        }
    ]
    assert 29 < city.links[0]["metres"] < 31
    assert sql("select count(*) from events where duplicate_of is not null") == [(0,)]
    assert sql("select count(*) from job_runs") == [(0,)]


def test_a_dry_run_refuses_a_writable_connection(db):
    with pytest.raises(ValueError, match="read-only"):
        dedupe.run(db, NOW, dry_run=True)


# End to end: ingest both sources, then dedupe -------------------------------------


def tm_event(event_id, name, start, skiddle_id, metres_north, segment, genre):
    """HAND-WRITTEN Ticketmaster event placed near a real Skiddle event's venue."""
    venue = SKIDDLE[skiddle_id]["venue"]
    return {
        "id": event_id,
        "name": name,
        "url": f"https://www.ticketmaster.co.uk/event/{event_id}",
        "dates": {"start": {"dateTime": start}, "status": {"code": "onsale"}},
        "classifications": [{"segment": {"name": segment}, "genre": {"name": genre}}],
        "priceRanges": [{"currency": "GBP", "min": 25.0, "max": 95.0}],
        "images": [
            {"ratio": "16_9", "url": f"https://s1.ticketm.net/{event_id}.jpg", "width": 1024}
        ],
        "_embedded": {
            "venues": [
                {
                    "id": f"tm-venue-{skiddle_id}",
                    "name": venue["name"],
                    "location": {
                        "latitude": str(north(venue["latitude"], metres_north)),
                        "longitude": str(venue["longitude"]),
                    },
                }
            ]
        },
    }


TM_PAGE = {
    "_embedded": {
        "events": [
            tm_event(
                "tm-hp",
                "Harry Potter and the Cursed Child",
                "2026-11-03T19:00:00Z",
                "42504394",
                30,
                "Arts & Theatre",
                "Theatre",
            ),
            tm_event(
                "tm-sheer", "Sheer Terror", "2026-11-02T18:15:00Z", "42623375", 20, "Music", "Rock"
            ),
            tm_event(
                "tm-efg",
                "EFG London Jazz Festival 2026",
                "2026-11-13T11:00:00Z",
                "42262752",
                10,
                "Music",
                "Jazz",
            ),
        ]
    },
    "page": {"size": 200, "totalElements": 3, "totalPages": 1, "number": 0},
}


def ingest_page(db, source, page):
    def handler(request):
        if (
            source == "ticketmaster"
            and request.url.params["startDateTime"] != "2026-11-01T00:00:00Z"
        ):
            return httpx.Response(
                200, json={"page": {"size": 200, "totalElements": 0, "totalPages": 0, "number": 0}}
            )
        return httpx.Response(200, json=page)

    return ingest.run(
        source,
        db,
        httpx.Client(transport=httpx.MockTransport(handler)),
        "KEY",
        now=NOW,
        dry_run=False,
        sleep=lambda s: None,
    )


def test_end_to_end_ingest_then_dedupe(db, sql):
    ingest_page(db, "skiddle", fixture("skiddle/events-page.json"))
    ingest_page(db, "ticketmaster", copy.deepcopy(TM_PAGE))
    summary = dedupe.run(db, NOW, dry_run=False)

    links = sorted((link["kept"], link["duplicate"]) for link in summary.cities[0].links)
    assert links == [
        ("ticketmaster:tm-efg", "skiddle:42262752"),
        ("ticketmaster:tm-hp", "skiddle:42504394"),
        ("ticketmaster:tm-sheer", "skiddle:42623375"),
    ]
    assert sql("select count(*) from events where duplicate_of is not null") == [(3,)]
