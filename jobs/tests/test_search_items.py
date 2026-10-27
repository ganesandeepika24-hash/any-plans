"""search_items (migration 010, decision 0010): visibility, filters, ordering, input
checks, weights from config, read-only and grants.

Needs TEST_DATABASE_URL. Times are anchored to T0, the database's hour two days ahead,
so every event sits clearly in the future unless a test puts it in the past.
"""

import math
import re
from datetime import datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

pytestmark = pytest.mark.db

CENTRE = (51.5074, -0.1278)
KM_LAT = 1 / 111.2  # degrees of latitude per km, near enough for clear margins


def vec(*head: float) -> str:
    """A 384-length vector literal starting with `head`, then zeros."""
    values = list(head) + [0.0] * (384 - len(head))
    return "[" + ",".join(str(v) for v in values) + "]"


QUERY = vec(1.0, 0.0)


@pytest.fixture
def t0(sql) -> datetime:
    return sql("select date_trunc('hour', now()) + interval '2 days'")[0][0]


@pytest.fixture(autouse=True)
def starting_weights(db_url):
    """Each test starts and ends on the migration's weights (version 1)."""

    def reset():
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute("select public.set_active_config('search_weights', 1)")
            conn.execute("delete from public.config where key = 'search_weights' and version > 1")

    reset()
    yield
    reset()


@pytest.fixture
def add(db, sql, t0):
    """Insert an event; `at` is hours after T0. Returns its id."""

    def insert(
        title,
        at=0.0,
        end=None,
        category="Music",
        price=(None, None),
        point=CENTRE,
        embedding=None,
        status="active",
        duplicate_of=None,
    ):
        start = t0 + timedelta(hours=at)
        end_at = None if end is None else t0 + timedelta(hours=end)
        geom = None if point is None else f"SRID=4326;POINT({point[1]} {point[0]})"
        return sql(
            """
            insert into events (city_id, source, source_id, title, category, start_at, end_at,
                                price_min, price_max, geom, embedding, status, duplicate_of,
                                last_checked_at)
            values ('london', 'test', %s, %s, %s, %s, %s, %s, %s,
                    %s::extensions.geography, %s::extensions.vector, %s, %s, now())
            returning id
            """,
            (title, title, category, start, end_at, *price, geom, embedding, status, duplicate_of),
        )[0][0]

    return insert


@pytest.fixture
def search(db_url):
    conn = psycopg.connect(db_url, autocommit=True, row_factory=dict_row)

    def run(filters="{}", embedding=None) -> list[dict]:
        if isinstance(filters, dict):
            filters = psycopg.types.json.Jsonb(filters)
        return conn.execute(
            "select *, now() as called_at"
            " from public.search_items(%s::jsonb, %s::extensions.vector)",
            (filters, embedding),
        ).fetchall()

    yield run
    conn.close()


def titles(rows) -> list[str]:
    return [r["title"] for r in rows]


def iso(moment: datetime) -> str:
    return moment.isoformat()


def set_weights(sql, similarity, distance, date, scale=7):
    version = sql("select max(version) + 1 from config where key = 'search_weights'")[0][0]
    sql(
        "insert into config (key, version, value_json, updated_by, is_active)"
        " values ('search_weights', %s, %s, 'test', false)",
        (
            version,
            psycopg.types.json.Jsonb(
                {
                    "similarity": similarity,
                    "distance": distance,
                    "date": date,
                    "date_scale_days": scale,
                }
            ),
        ),
    )
    sql("select set_active_config('search_weights', %s)", (version,))


# Visibility --------------------------------------------------------------------


@pytest.fixture
def visibility_cases(add, sql):
    kept = add("future")
    add("cancelled", status="cancelled")
    add("status past", status="past")
    add("duplicate", duplicate_of=kept)
    # Relative to now, not T0: these straddle the 3-hour fallback and the end time.
    # 2h and 3.5h sit either side of 3 hours, so a 2- or 4-hour fallback fails.
    sql(
        """
        insert into events (city_id, source, source_id, title, category, start_at, end_at)
        values ('london', 'test', 'ended', 'ended', 'Music',
                now() - interval '2 days', now() - interval '1 day'),
               ('london', 'test', 'running', 'running', 'Arts',
                now() - interval '2 days', now() + interval '2 days'),
               ('london', 'test', 'no end, 2h ago', 'no end, 2h ago', 'Music',
                now() - interval '2 hours', null),
               ('london', 'test', 'no end, 3.5h ago', 'no end, 3.5h ago', 'Music',
                now() - interval '3 hours 30 minutes', null)
        """
    )


def test_visible_rows_match_events_public_read(db_url, search, visibility_cases):
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("set role anon")
        anon = {r[0] for r in conn.execute("select title from events")}
    assert anon == {"future", "running", "no end, 2h ago"}
    assert set(titles(search({"limit": 100}))) == anon


def test_known_categories_match_the_events_check(sql):
    check = sql(
        "select pg_get_constraintdef(oid) from pg_constraint"
        " where conrelid = 'public.events'::regclass and contype = 'c'"
        " and pg_get_constraintdef(oid) like '%category%'"
    )[0][0]
    source = sql("select prosrc from pg_proc where proname = 'search_items'")[0][0]
    in_function = re.search(r"known_categories constant text\[\] := array\[(.*?)\];", source, re.S)
    assert set(re.findall(r"'([^']+)'", in_function.group(1))) == set(
        re.findall(r"'([^']+)'::text", check)
    )


# Filters -----------------------------------------------------------------------


def test_dates_keep_events_whose_span_overlaps(add, search, t0):
    add("before", at=12)
    add("inside", at=30)
    add("after", at=60)
    add("starts at date_to", at=48)
    add("spans the window", at=-24, end=60)
    add("ends at date_from", at=20, end=24)
    add("no end, overlaps by fallback", at=22)  # counts as ending at 25
    rows = search(
        {"date_from": iso(t0 + timedelta(hours=24)), "date_to": iso(t0 + timedelta(hours=48))}
    )
    assert set(titles(rows)) == {"inside", "spans the window", "no end, overlaps by fallback"}


def test_categories(add, search):
    add("gig", category="Music")
    add("standup", category="Comedy", at=1)
    add("show", category="Arts", at=2)
    assert set(titles(search({"categories": ["Comedy", "Arts"]}))) == {"standup", "show"}


@pytest.fixture
def priced(add):
    add("free", price=(0, 0))
    add("cheap", price=(5, 10), at=1)
    add("mid", price=(15, 30), at=2)
    add("pricey", price=(50, 80), at=3)
    add("only min", price=(20, None), at=4)
    add("only max", price=(None, 12), at=5)
    add("unpriced", at=6)


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"price_max": 12}, {"free", "cheap", "only max", "unpriced"}),
        ({"price_min": 25, "price_max": 60}, {"mid", "pricey"} | {"unpriced"}),
        ({"price_max": 0}, {"free", "unpriced"}),
        ({"price_max": 0, "include_unpriced": False}, {"free"}),
        ({"price_min": 25, "price_max": 60, "include_unpriced": False}, {"mid", "pricey"}),
        (
            {"include_unpriced": False},
            {"free", "cheap", "mid", "pricey", "only min", "only max"},
        ),
        ({}, {"free", "cheap", "mid", "pricey", "only min", "only max", "unpriced"}),
    ],
    ids=[
        "max",
        "range",
        "free",
        "free, priced only",
        "range, priced only",
        "priced only",
        "none",
    ],
)
def test_prices(priced, search, filters, expected):
    assert set(titles(search(filters))) == expected


def test_radius_keeps_events_within_it_and_drops_those_without_a_location(add, search):
    lat, lon = CENTRE
    add("0.5 km", point=(lat + 0.5 * KM_LAT, lon))
    add("1.5 km", point=(lat + 1.5 * KM_LAT, lon), at=1)
    add("nowhere", point=None, at=2)
    rows = search({"lat": lat, "lon": lon, "radius_km": 1})
    assert titles(rows) == ["0.5 km"]
    assert rows[0]["distance_m"] == pytest.approx(500, rel=0.01)
    assert set(titles(search())) == {"0.5 km", "1.5 km", "nowhere"}


def test_filters_combine(add, search, t0):
    lat, lon = CENTRE
    add("match", category="Comedy", price=(5, 10), at=30)
    add("wrong category", category="Music", price=(5, 10), at=30)
    add("too far", category="Comedy", price=(5, 10), at=30, point=(lat + 3 * KM_LAT, lon))
    add("too dear", category="Comedy", price=(50, 60), at=30)
    add("too late", category="Comedy", price=(5, 10), at=80)
    rows = search(
        {
            "date_from": iso(t0 + timedelta(hours=24)),
            "date_to": iso(t0 + timedelta(hours=48)),
            "categories": ["Comedy"],
            "price_max": 20,
            "lat": lat,
            "lon": lon,
            "radius_km": 2,
        }
    )
    assert titles(rows) == ["match"]


def test_results_carry_freshness_and_source(add, search):
    add("gig")
    (row,) = search()
    assert row["source"] == "test" and row["last_checked_at"] is not None


def test_limit_defaults_to_20_and_caps_at_the_value_given(add, search):
    for i in range(25):
        add(f"e{i:02}", at=i)
    assert len(search()) == 20
    assert len(search({"limit": 3})) == 3


# Ordering ----------------------------------------------------------------------


def test_similarity_alone_orders_by_meaning(add, search, sql):
    set_weights(sql, 1, 0, 0)
    add("opposite", embedding=vec(-1.0, 0.0))
    add("orthogonal", embedding=vec(0.0, 1.0), at=1)
    add("aligned", embedding=vec(1.0, 0.0), at=2)
    add("not embedded", at=3)
    rows = search(embedding=QUERY)
    # Orthogonal and not embedded both score 0; the earlier start goes first.
    assert titles(rows) == ["aligned", "orthogonal", "not embedded", "opposite"]
    assert [r["similarity"] for r in rows] == [
        pytest.approx(1),
        pytest.approx(0),
        None,
        pytest.approx(-1),
    ]


def test_distance_alone_orders_nearest_first(add, search, sql):
    set_weights(sql, 0, 1, 0)
    lat, lon = CENTRE
    add("4 km", point=(lat + 4 * KM_LAT, lon))
    add("1 km", point=(lat + 1 * KM_LAT, lon), at=1)
    add("2 km", point=(lat + 2 * KM_LAT, lon), at=2)
    assert titles(search({"lat": lat, "lon": lon, "radius_km": 5})) == ["1 km", "2 km", "4 km"]


def test_date_alone_orders_soonest_first(add, search, sql):
    set_weights(sql, 0, 0, 1)
    add("in 3 days", at=72)
    add("at T0", at=0)
    add("in 1 day", at=24)
    assert titles(search()) == ["at T0", "in 1 day", "in 3 days"]


def test_mixed_score_matches_the_formula(add, search):
    lat, lon = CENTRE
    add("a", embedding=vec(0.6, 0.8), point=(lat + 1 * KM_LAT, lon), at=5)
    add("b", embedding=vec(1.0, 0.0), point=(lat + 3 * KM_LAT, lon), at=50)
    add("c", embedding=vec(0.0, 1.0), point=(lat + 0.2 * KM_LAT, lon), at=200)
    rows = search({"lat": lat, "lon": lon, "radius_km": 5}, embedding=QUERY)
    assert len(rows) == 3
    for r in rows:
        days = max((r["start_at"] - r["called_at"]).total_seconds(), 0) / 86400
        expected = (
            0.6 * r["similarity"] + 0.25 * (1 - r["distance_m"] / 5000) + 0.15 * math.exp(-days / 7)
        )
        assert r["score"] == pytest.approx(expected, abs=1e-6)
    assert [r["score"] for r in rows] == sorted((r["score"] for r in rows), reverse=True)


def test_ties_go_to_the_earlier_start_then_the_lower_id(add, search, sql):
    set_weights(sql, 0, 0, 0)
    later = add("later", at=5)
    first = add("first", at=1)
    second = add("second", at=1)
    rows = search()
    assert [r["id"] for r in rows] == [*sorted([first, second]), later]


def test_dates_count_from_the_search_start(add, search, sql, t0):
    set_weights(sql, 0, 0, 1)
    add("at window start", at=24)
    rows = search(
        {"date_from": iso(t0 + timedelta(hours=24)), "date_to": iso(t0 + timedelta(hours=48))}
    )
    assert rows[0]["score"] == pytest.approx(1)


def test_missing_inputs_drop_their_terms(add, search):
    add("gig", embedding=vec(1.0, 0.0))
    (row,) = search()  # no query embedding, no point
    assert row["similarity"] is None and row["distance_m"] is None
    days = max((row["start_at"] - row["called_at"]).total_seconds(), 0) / 86400
    assert row["score"] == pytest.approx(0.15 * math.exp(-days / 7), abs=1e-6)


def test_changing_the_config_version_changes_the_order(add, search, sql):
    add("soon, unrelated", embedding=vec(0.0, 1.0), at=0)
    add("later, on topic", embedding=vec(1.0, 0.0), at=240)
    set_weights(sql, 0, 0, 1)
    assert titles(search(embedding=QUERY))[0] == "soon, unrelated"
    set_weights(sql, 1, 0, 0)
    assert titles(search(embedding=QUERY))[0] == "later, on topic"


def test_no_active_weights_is_an_error(search, sql):
    sql("update config set is_active = false where key = 'search_weights'")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no active search_weights"):
        search()


@pytest.mark.parametrize(
    "weights",
    [
        {"similarity": 1, "distance": 0, "date": 0},
        {"similarity": -1, "distance": 0, "date": 0, "date_scale_days": 7},
        {"similarity": 1, "distance": 0, "date": 0, "date_scale_days": 0},
        {"similarity": "1", "distance": 0, "date": 0, "date_scale_days": 7},
    ],
    ids=["no scale", "negative weight", "zero scale", "string weight"],
)
def test_bad_weights_are_an_error(search, sql, weights):
    sql(
        "insert into config (key, version, value_json, updated_by, is_active)"
        " values ('search_weights', 99, %s, 'test', false)",
        (psycopg.types.json.Jsonb(weights),),
    )
    sql("select set_active_config('search_weights', 99)")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="search_weights needs"):
        search()


# Input checks ------------------------------------------------------------------


@pytest.mark.parametrize(
    "filters",
    [
        {"when": "tonight"},
        [],
        {"date_from": "2026-11-01T19:00:00"},
        {"date_from": 20261101},
        {"date_from": "2026-11-02T00:00:00Z", "date_to": "2026-11-01T00:00:00Z"},
        {"categories": []},
        {"categories": ["Jazz"]},
        {"categories": [1]},
        {"categories": "Music"},
        {"price_max": -1},
        {"price_max": "10"},
        {"price_min": 20, "price_max": 10},
        {"include_unpriced": "yes"},
        {"lat": 51.5, "lon": -0.1},
        {"lat": 51.5, "lon": -0.1, "radius_km": 0},
        {"lat": 51.5, "lon": -0.1, "radius_km": 101},
        {"lat": 91, "lon": -0.1, "radius_km": 1},
        {"limit": 0},
        {"limit": 101},
        {"limit": 2.5},
        {"limit": "5"},
    ],
    ids=[
        "unknown key",
        "not an object",
        "no offset",
        "date not a string",
        "from after to",
        "no categories",
        "unknown category",
        "category not a string",
        "categories not an array",
        "negative price",
        "price as string",
        "min over max",
        "include_unpriced not boolean",
        "point without radius",
        "zero radius",
        "radius over 100",
        "latitude out of range",
        "limit 0",
        "limit 101",
        "fractional limit",
        "limit as string",
    ],
)
def test_bad_filters_are_refused(search, filters):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="search_items:"):
        search(psycopg.types.json.Jsonb(filters))


def test_a_query_embedding_must_have_384_dimensions(search):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="384 dimensions, not 3"):
        search(embedding="[1,0,0]")


def test_null_filters_mean_no_filters(add, search):
    add("gig")
    assert titles(search(None)) == ["gig"]


# Read-only and grants ----------------------------------------------------------


def test_works_in_a_read_only_transaction(db_url, add):
    add("gig")
    with psycopg.connect(db_url) as conn:
        conn.read_only = True
        rows = conn.execute("select title from public.search_items('{}')").fetchall()
    assert rows == [("gig",)]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_only_service_role_may_call_it(db_url, add, role):
    add("gig")
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(f"set role {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select * from public.search_items('{}')")
        conn.execute("set role service_role")
        assert conn.execute("select title from public.search_items('{}')").fetchall() == [("gig",)]
