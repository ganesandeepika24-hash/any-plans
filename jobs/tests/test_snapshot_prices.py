"""Price snapshots (decision 0009) and the delete job's snapshot count, against a database
with migrations 001, 005 and 006 and the seeds. CI sets TEST_DATABASE_URL."""

from datetime import UTC, date, datetime, timedelta

import pytest

from jobs import delete_ended, snapshot_prices
from jobs.db import Database
from tests.conftest import NOW

pytestmark = pytest.mark.db

HOUR = timedelta(hours=1)


def add_event(
    sql, source_id, start_at, end_at=None, price=(10, 20), status="active", venue_id=None
):
    return sql(
        "insert into events (city_id, source, source_id, title, category, start_at, end_at,"
        " price_min, price_max, currency, status, venue_id)"
        " values ('london', 'test', %s, 'x', 'Music', %s, %s, %s, %s, %s, %s, %s) returning id",
        (
            source_id,
            start_at,
            end_at,
            price[0] if price else None,
            price[1] if price else None,
            "GBP" if price else None,
            status,
            venue_id,
        ),
    )[0][0]


def add_snapshot(sql, event_id, day):
    sql(
        "insert into price_snapshots (event_id, snapshot_date, price_min, price_max, currency,"
        " status) values (%s, %s, 1, 2, 'GBP', 'active')",
        (event_id, day),
    )


def snapshots(sql):
    return sql(
        "select e.source_id, s.snapshot_date, s.price_min, s.price_max, s.status"
        " from price_snapshots s join events e on e.id = s.event_id order by 1, 2"
    )


@pytest.fixture
def mixed_events(sql):
    add_event(sql, "priced", NOW + 24 * HOUR)
    add_event(sql, "cancelled", NOW + 48 * HOUR, status="cancelled")
    add_event(sql, "no-price", NOW + 24 * HOUR, price=None)
    add_event(sql, "only-max", NOW + 24 * HOUR, price=(None, 15))
    add_event(sql, "ended", NOW - 5 * HOUR, end_at=NOW - HOUR)
    add_event(sql, "no-end-over", NOW - 4 * HOUR)  # ended under the 3-hour rule
    add_event(sql, "no-end-on", NOW - HOUR)  # still on


def test_only_current_priced_events_are_snapshotted(db, sql, mixed_events):
    summary = snapshot_prices.run(db, NOW, dry_run=False)

    day = date(2026, 11, 1)
    assert snapshots(sql) == [
        ("cancelled", day, 10, 20, "cancelled"),
        ("no-end-on", day, 10, 20, "active"),
        ("only-max", day, None, 15, "active"),
        ("priced", day, 10, 20, "active"),
    ]
    assert (summary.counts.fetched, summary.counts.new, summary.counts.updated) == (4, 4, 0)
    assert summary.days == {"london": "2026-11-01"}
    assert sql(
        "select job, city_id, fetched, new, updated, duration_ms is not null from job_runs"
    ) == [("snapshot_prices", None, 4, 4, 0, True)]


def test_a_rerun_the_same_day_updates_instead_of_duplicating(db, sql, mixed_events):
    snapshot_prices.run(db, NOW, dry_run=False)
    sql("update events set price_min = 12, price_max = 30 where source_id = 'priced'")

    summary = snapshot_prices.run(db, NOW + HOUR, dry_run=False)

    assert (summary.counts.new, summary.counts.updated) == (0, 4)
    assert sql("select count(*) from price_snapshots") == [(4,)]
    assert sql(
        "select s.price_min, s.price_max, s.captured_at from price_snapshots s"
        " join events e on e.id = s.event_id where e.source_id = 'priced'"
    ) == [(12, 30, NOW + HOUR)]


def test_the_next_day_adds_new_rows(db, sql, mixed_events):
    snapshot_prices.run(db, NOW, dry_run=False)
    summary = snapshot_prices.run(db, NOW + 24 * HOUR, dry_run=False)
    # "no-end-on" has ended by then; the other three get a second day.
    assert (summary.counts.new, summary.counts.updated) == (3, 0)
    assert sql("select count(*) from price_snapshots") == [(7,)]


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 6, 30, 23, 30, tzinfo=UTC), date(2026, 7, 1)),  # BST: already 1 July
        (datetime(2026, 11, 30, 23, 30, tzinfo=UTC), date(2026, 11, 30)),  # GMT: still 30 Nov
        (datetime(2026, 7, 1, 0, 30, tzinfo=UTC), date(2026, 7, 1)),
    ],
    ids=["bst-before-midnight-utc", "gmt-before-midnight-utc", "after-midnight-utc"],
)
def test_the_snapshot_date_is_the_london_date(db, sql, now, expected):
    add_event(sql, "soon", now + HOUR)
    summary = snapshot_prices.run(db, now, dry_run=False)
    assert sql("select snapshot_date from price_snapshots") == [(expected,)]
    assert summary.days == {"london": expected.isoformat()}


def test_dry_run_counts_and_writes_nothing(db_url, db, sql, mixed_events):
    read_only = Database.connect(db_url, read_only=True)
    try:
        first = snapshot_prices.run(read_only, NOW, dry_run=True)
        assert (first.counts.fetched, first.counts.new, first.counts.updated) == (4, 4, 0)
        assert sql("select count(*) from price_snapshots") == [(0,)]
        assert sql("select count(*) from job_runs") == [(0,)]

        snapshot_prices.run(db, NOW, dry_run=False)
        second = snapshot_prices.run(read_only, NOW, dry_run=True)
        assert (second.counts.new, second.counts.updated) == (0, 4)
    finally:
        read_only.close()
    assert sql("select count(*) from job_runs") == [(1,)]  # only the real run


def test_a_dry_run_refuses_a_writable_connection(db):
    with pytest.raises(ValueError, match="read-only"):
        snapshot_prices.run(db, NOW, dry_run=True)


def test_a_crashed_run_keeps_duration_null(db, sql, mixed_events, monkeypatch):
    def fail(*args):
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(db, "snapshot_prices", fail)
    with pytest.raises(RuntimeError):
        snapshot_prices.run(db, NOW, dry_run=False)
    assert sql("select job, duration_ms from job_runs") == [("snapshot_prices", None)]
    assert sql("select count(*) from price_snapshots") == [(0,)]


def test_the_delete_job_reports_events_venues_and_snapshots(db, sql):
    orphan = sql(
        "insert into venues (city_id, source, source_id, name, kind)"
        " values ('london', 'ticketmaster', 'v-orphan', 'Orphan', 'other') returning id"
    )
    assert orphan
    ended_a = add_event(sql, "ended-a", NOW - 5 * HOUR, end_at=NOW - HOUR)
    add_event(sql, "ended-b", NOW - 6 * HOUR, end_at=NOW - 2 * HOUR)
    current = add_event(sql, "current", NOW + 24 * HOUR)
    for day in (date(2026, 10, 29), date(2026, 10, 30), date(2026, 10, 31)):
        add_snapshot(sql, ended_a, day)
    for day in (date(2026, 10, 31), date(2026, 11, 1)):
        add_snapshot(sql, current, day)

    counts = delete_ended.run(db, NOW)

    assert counts.deleted == 2 + 1 + 3
    assert sql("select job, deleted from job_runs") == [("delete_ended_events", 6)]
    assert [r[0] for r in sql("select source_id from events")] == ["current"]
    assert sql("select count(*) from price_snapshots where event_id = %s", (current,)) == [(2,)]
    assert sql("select count(*) from price_snapshots") == [(2,)]


def test_the_delete_job_prints_three_counts(db, sql, capsys):
    ended = add_event(sql, "ended", NOW - 5 * HOUR, end_at=NOW - HOUR)
    add_snapshot(sql, ended, date(2026, 10, 31))
    delete_ended.run(db, NOW)
    assert capsys.readouterr().out.strip() == (
        '{"events_deleted": 1, "venues_deleted": 0, "snapshots_deleted": 1}'
    )
