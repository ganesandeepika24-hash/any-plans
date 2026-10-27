"""Integration tests against a database with all migrations applied and the seeds.

CI sets TEST_DATABASE_URL to the postgis service container. Locally they skip without it.
"""

import copy
from datetime import timedelta

import httpx
import psycopg
import pytest

from jobs import delete_ended, embed_events, ingest
from jobs.db import Database
from tests.conftest import NOW, FakeEmbedder, fixture

pytestmark = pytest.mark.db

FIRST_WINDOW = "2026-11-01T00:00:00Z"


def tm_server(page=None, fail_window=None):
    page = page or fixture("ticketmaster/events-page.json")

    def handler(request: httpx.Request) -> httpx.Response:
        start = request.url.params["startDateTime"]
        if start == fail_window:
            return httpx.Response(503)
        if start == FIRST_WINDOW:
            return httpx.Response(200, json=page)
        return httpx.Response(
            200, json={"page": {"size": 200, "totalElements": 0, "totalPages": 0, "number": 0}}
        )

    return handler


def run_tm(db, server=None, dry_run=False, now=NOW):
    return ingest.run(
        "ticketmaster",
        db,
        httpx.Client(transport=httpx.MockTransport(server or tm_server())),
        "KEY",
        now=now,
        dry_run=dry_run,
        sleep=lambda s: None,
    )


def test_ingest_writes_events_venues_and_a_job_run(db, sql):
    summary = run_tm(db)
    assert (summary.counts.fetched, summary.counts.new, summary.counts.updated) == (10, 6, 0)
    assert summary.calls == 13 and summary.complete and summary.swept

    rows = dict(sql("select source_id, status from events where source = 'ticketmaster'"))
    assert rows == {
        "tm-jazz": "active",
        "tm-no-time": "active",
        "tm-multi-day": "active",
        "tm-cancelled": "cancelled",
        "tm-no-price": "active",
        "tm-inverted-price": "active",
    }
    assert sql("select count(*) from venues where source = 'ticketmaster' and kind = 'other'") == [
        (2,)
    ]
    # Embedding is a separate job (embed_events), run by ingest.main after dedupe.
    assert sql("select count(*) from events where embedding is not null") == [(0,)]
    assert sql("select count(*) from events where geom is null") == [(0,)]

    run = sql(
        "select job, city_id, duration_ms is not null, fetched, new, updated, failed, deleted"
        " from job_runs"
    )
    assert run == [("ingest_ticketmaster", "london", True, 10, 6, 0, 0, 0)]
    assert sql("select calls, daily_limit from usage_daily where service = 'ticketmaster'") == [
        (13, 4500)
    ]


def test_a_second_run_updates_and_only_re_embeds_changed_text(db, sql):
    run_tm(db)
    embed_events.run(db, NOW, dry_run=False, embedder=FakeEmbedder())
    page = fixture("ticketmaster/events-page.json")
    page["_embedded"]["events"][0]["name"] = "Late Night Jazz (extra show)"
    summary = run_tm(db, tm_server(page))
    embedder = FakeEmbedder()
    embedded = embed_events.run(db, NOW, dry_run=False, embedder=embedder)

    assert (summary.counts.new, summary.counts.updated) == (0, 6)
    # The upsert keeps the old vector until the embedding job replaces it.
    assert sql("select count(*) from events where status = 'active' and embedding is null") == [
        (0,)
    ]
    assert embedder.texts == ["Late Night Jazz (extra show) · Music · jazz · Ronnie Scott's"]
    assert (embedded.counts.new, embedded.counts.updated, embedded.unchanged) == (0, 1, 4)
    assert sql("select calls from usage_daily where service = 'ticketmaster'") == [(26,)]
    assert sql("select job from job_runs order by id") == [
        ("ingest_ticketmaster",),
        ("embed_events",),
        ("ingest_ticketmaster",),
        ("embed_events",),
    ]


def test_postponed_events_are_deleted(db, sql):
    page = fixture("ticketmaster/events-page.json")
    postponed = next(e for e in page["_embedded"]["events"] if e["id"] == "tm-postponed")
    first = copy.deepcopy(page)
    next(e for e in first["_embedded"]["events"] if e["id"] == "tm-postponed")["dates"]["status"][
        "code"
    ] = "onsale"
    run_tm(db, tm_server(first))
    assert sql("select count(*) from events where source_id = 'tm-postponed'") == [(1,)]

    summary = run_tm(db, tm_server(page))
    assert postponed["dates"]["status"]["code"] == "postponed"
    assert sql("select count(*) from events where source_id = 'tm-postponed'") == [(0,)]
    assert summary.counts.deleted >= 1


def vanished_event(sql):
    sql(
        """insert into events (city_id, source, source_id, title, category, start_at,
                               last_checked_at)
           values ('london', 'ticketmaster', 'tm-vanished', 'Gone', 'Music',
                   %s, %s)""",
        (NOW + timedelta(days=30), NOW - timedelta(days=1)),
    )


def test_the_sweep_deletes_events_a_clean_run_no_longer_sees(db, sql):
    vanished_event(sql)
    summary = run_tm(db)
    assert summary.swept
    assert sql("select count(*) from events where source_id = 'tm-vanished'") == [(0,)]
    assert sql("select deleted from job_runs") == [(1,)]


def test_no_sweep_after_a_failed_window(db, sql):
    vanished_event(sql)
    summary = run_tm(db, tm_server(fail_window="2026-11-08T00:00:00Z"))
    assert not summary.complete and not summary.swept
    [failure] = summary.failed_windows
    assert (failure["window"], failure["status"], failure["page"]) == (
        "2026-11-08T00:00:00Z..2026-11-15T00:00:00Z",
        503,
        0,
    )
    assert "Service Unavailable" in failure["message"]
    assert sql("select count(*) from events where source_id = 'tm-vanished'") == [(1,)]


def test_no_sweep_when_the_daily_stop_is_hit(db, sql):
    vanished_event(sql)
    sql(
        "insert into usage_daily (date, service, calls, daily_limit)"
        " values (%s, 'ticketmaster', 4495, 4500)",
        (NOW.date(),),
    )
    summary = run_tm(db)
    assert summary.cap_reached == "daily stop of 4500 calls" and not summary.swept
    assert sql("select count(*) from events where source_id = 'tm-vanished'") == [(1,)]
    assert sql("select calls from usage_daily where service = 'ticketmaster'") == [(4500,)]


def test_a_crashed_run_keeps_duration_null_and_still_records_calls(db, sql, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setattr(db, "sweep_vanished", fail)  # after every fetch and upsert
    with pytest.raises(RuntimeError):
        run_tm(db)
    assert sql("select duration_ms from job_runs") == [(None,)]
    assert sql("select calls from usage_daily where service = 'ticketmaster'") == [(13,)]


def test_dry_run_writes_nothing(db_url, db, sql):
    read_only = Database.connect(db_url, read_only=True)
    try:
        summary = run_tm(read_only, dry_run=True)
    finally:
        read_only.close()
    assert summary.events_in_radius == 6 and summary.calls == 13
    for table in ("events", "venues", "job_runs", "usage_daily"):
        assert sql(f"select count(*) from {table}") == [(0,)], table  # noqa: S608


def test_a_read_only_connection_rejects_writes(db_url):
    read_only = Database.connect(db_url, read_only=True)
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            read_only.start_run("ingest_ticketmaster", "london", NOW)
    finally:
        read_only.close()


def test_a_dry_run_refuses_a_writable_connection(db):
    with pytest.raises(ValueError, match="read-only"):
        run_tm(db, dry_run=True)


def test_skiddle_ingest(db, sql):
    page = fixture("skiddle/events-page.json")  # trimmed real events
    summary = ingest.run(
        "skiddle",
        db,
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=page))),
        "KEY",
        now=NOW,
        dry_run=False,
        sleep=lambda s: None,
    )
    # The same page comes back for each of the 13 windows; duplicates are dropped.
    assert (summary.calls, summary.complete, summary.failed_windows) == (13, True, [])
    assert (summary.counts.new, summary.swept) == (26, False)
    assert len(summary.windows) == 13 and summary.page_sizes == [100]
    assert sql(
        "select price_min, price_max, currency from events where source_id = '42504394'"
    ) == [(30, 210, "GBP")]
    assert sql("select count(*) from events where source = 'skiddle' and genre is not null") == [
        (0,)
    ]
    assert sql("select calls, daily_limit from usage_daily where service = 'skiddle'") == [
        (13, 1000)
    ]


USED, ORPHAN, SKIDDLE = (f"00000000-0000-0000-0000-00000000000{c}" for c in "abc")


def test_delete_ended_events_and_orphaned_ticketmaster_venues(db, sql):
    for venue_id, source, source_id in (
        (USED, "ticketmaster", "v-used"),
        (ORPHAN, "ticketmaster", "v-orphan"),
        (SKIDDLE, "skiddle", "v-sk"),
    ):
        sql(
            "insert into venues (id, city_id, source, source_id, name, kind)"
            " values (%s, 'london', %s, %s, 'Venue', 'other')",
            (venue_id, source, source_id),
        )
    sql("insert into curated_pages (venue_id, url) values (%s, 'https://example.com')", (ORPHAN,))
    hour = timedelta(hours=1)
    for source_id, start_at, end_at, venue_id in (
        ("ended", NOW - 5 * hour, NOW - hour, None),
        ("no-end-old", NOW - 4 * hour, None, None),
        ("no-end-recent", NOW - hour, None, None),
        ("future", NOW + 24 * hour, None, USED),
    ):
        sql(
            "insert into events (city_id, source, source_id, title, category, start_at, end_at,"
            " venue_id) values ('london', 't', %s, 'x', 'Music', %s, %s, %s)",
            (source_id, start_at, end_at, venue_id),
        )

    counts = delete_ended.run(db, NOW)

    assert counts.deleted == 3
    assert sorted(r[0] for r in sql("select source_id from events")) == ["future", "no-end-recent"]
    assert sorted(r[0] for r in sql("select source_id from venues")) == ["v-sk", "v-used"]
    assert sql("select count(*) from curated_pages") == [(0,)]
    assert sql("select job, city_id, deleted, duration_ms is not null from job_runs") == [
        ("delete_ended_events", None, 3, True)
    ]
