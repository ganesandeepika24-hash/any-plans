"""Takedown of one Ticketmaster event (decision 0001, migration 025, jobs.takedown).

The events come from ingesting the Ticketmaster fixture page (trimmed real events), so
the takedown runs on a copy of a real event and its venue. Around it: price snapshots,
a Skiddle copy that dedupe hid behind it, and a Skiddle event whose source_id is the
same string. Only the target and its own rows may go.
"""

from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from jobs import takedown
from jobs.db import Database
from tests.conftest import NOW
from tests.test_db_integration import run_tm

pytestmark = pytest.mark.db

TARGET = "tm-jazz"
REQUESTED = NOW - timedelta(hours=3)


def snapshot(sql) -> dict[str, list[tuple]]:
    """Every row the takedown could touch, in a comparable form."""
    return {
        "events": sql(
            "select e.source, e.source_id, d.source_id from events e"
            " left join events d on d.id = e.duplicate_of order by 1, 2"
        ),
        "price_snapshots": sql(
            "select e.source, e.source_id, p.snapshot_date from price_snapshots p"
            " join events e on e.id = p.event_id order by 1, 2, 3"
        ),
        "venues": sql("select source, source_id from venues order by 1, 2"),
        "takedowns": sql("select source, source_id, reference from takedowns order by 1, 2"),
    }


@pytest.fixture
def stored(db, sql):
    """The fixture page ingested, plus the rows around the target."""
    run_tm(db)
    target = sql(
        "select id from events where source = 'ticketmaster' and source_id = %s", (TARGET,)
    )
    ((target_id,),) = target
    for day in (date(2026, 10, 30), date(2026, 10, 31)):
        sql(
            "insert into price_snapshots (event_id, snapshot_date, price_min, price_max,"
            " currency, status) values (%s, %s, 25, 40, 'GBP', 'active')",
            (target_id, day),
        )
    other_tm = sql(
        "select id from events where source = 'ticketmaster' and source_id = 'tm-no-price'"
    )[0][0]
    sql(
        "insert into price_snapshots (event_id, snapshot_date, status) values (%s, %s, 'active')",
        (other_tm, date(2026, 10, 31)),
    )
    for source_id, duplicate_of in (("skiddle-jazz-copy", target_id), (TARGET, None)):
        # A Skiddle copy hidden behind the target, and a Skiddle event whose source_id is
        # the same string as the target's.
        sql(
            "insert into events (city_id, source, source_id, title, category, start_at,"
            " duplicate_of) values ('london', 'skiddle', %s, %s, 'Music', %s, %s)",
            (source_id, source_id, NOW + timedelta(days=3), duplicate_of),
        )
    return target_id


def take_down(db, source_id=TARGET, **kw):
    return takedown.run(db, source_id, "TM-REQ-123", REQUESTED, NOW, **kw)


def test_a_takedown_removes_only_the_event_and_its_linked_rows(db, sql, stored):
    before = snapshot(sql)
    result = take_down(db)
    assert (result.status, result.removed) == (
        "removed",
        {"events": 1, "snapshots": 2, "duplicates_shown": 1, "venues": 0},
    )
    after = snapshot(sql)
    # The event and its two snapshots are gone. The Skiddle copy is shown again. The
    # Skiddle event with the same source_id stays. Every other row is as it was.
    changed = {("ticketmaster", TARGET), ("skiddle", "skiddle-jazz-copy")}
    assert after["events"] == sorted(
        [r for r in before["events"] if r[:2] not in changed]
        + [("skiddle", "skiddle-jazz-copy", None)],
        key=lambda r: (r[0], r[1]),
    )
    assert ("skiddle", "skiddle-jazz-copy", TARGET) in before["events"]
    assert ("skiddle", TARGET, None) in after["events"]
    assert after["price_snapshots"] == [
        r for r in before["price_snapshots"] if r[:2] != ("ticketmaster", TARGET)
    ]
    assert len(before["price_snapshots"]) - len(after["price_snapshots"]) == 2
    assert after["venues"] == before["venues"]  # two more events still use its venue
    assert after["takedowns"] == [("ticketmaster", TARGET, "TM-REQ-123")]


def test_the_venue_goes_when_no_event_is_left_at_it(db, sql, stored):
    venue = sql(
        "select venue_id from events where source_id = %s and source = 'ticketmaster'", (TARGET,)
    )
    sql(
        "delete from events where venue_id = %s and source_id <> %s",
        (venue[0][0], TARGET),
    )
    assert take_down(db).removed["venues"] == 1
    assert sql("select count(*) from venues where id = %s", (venue[0][0],)) == [(0,)]
    assert sql("select count(*) from venues") == [(1,)]  # the other venue stays


def test_a_taken_down_event_cant_come_back(db, sql, stored):
    take_down(db)
    summary = run_tm(db)  # the same page again, as a later manual ingest would fetch it
    assert summary.counts.failed == 0
    rows = {r[0] for r in sql("select source_id from events where source = 'ticketmaster'")}
    assert TARGET not in rows and len(rows) == 5
    assert sql(
        "select count(*) from events where source = 'skiddle' and source_id = %s", (TARGET,)
    ) == [(1,)]


def test_the_trigger_skips_an_update_too(db, sql, stored):
    # A row taken down by hand in SQL, and then upserted: the update is skipped as well.
    sql(
        "insert into takedowns (source, source_id, reference, requested_at)"
        " values ('ticketmaster', 'tm-no-price', 'manual', now())"
    )
    sql("update events set title = 'changed' where source_id = 'tm-no-price'")
    assert sql("select title from events where source_id = 'tm-no-price'") != [("changed",)]


def test_a_dry_run_changes_nothing(db_url, sql, stored):
    before = snapshot(sql)
    read_only = Database.connect(db_url, read_only=True)
    try:
        result = take_down(read_only, dry_run=True)
    finally:
        read_only.close()
    assert result.status == "would_remove" and snapshot(sql) == before
    assert sql("select count(*) from job_runs where job = 'takedown'") == [(0,)]


def test_a_dry_run_refuses_a_writable_connection(db, stored):
    with pytest.raises(ValueError, match="read-only"):
        take_down(db, dry_run=True)


def test_an_unknown_id_fails_and_changes_nothing(db, sql, stored):
    before = snapshot(sql)
    with pytest.raises(takedown.TakedownError, match="--block-only"):
        take_down(db, source_id="tm-unknown")
    assert snapshot(sql) == before


def test_block_only_records_an_event_not_stored_and_blocks_it(db, sql, stored):
    sql("delete from events where source = 'ticketmaster' and source_id = %s", (TARGET,))
    result = take_down(db, block_only=True)
    assert (result.status, result.removed["events"]) == ("blocked", 0)
    run_tm(db)
    assert sql(
        "select count(*) from events where source = 'ticketmaster' and source_id = %s", (TARGET,)
    ) == [(0,)]


def test_running_twice_is_harmless(db, sql, stored):
    take_down(db)
    once = snapshot(sql)
    assert take_down(db).status == "already_removed"
    assert snapshot(sql) == once


def test_a_job_run_is_written(db, sql, stored):
    take_down(db)
    rows = sql(
        "select job, city_id, deleted, duration_ms is not null from job_runs where job = 'takedown'"
    )
    assert rows == [("takedown", None, 3, True)]  # the event and its two snapshots


@pytest.mark.parametrize(
    ("source_id", "reference", "requested_at", "why"),
    [
        ("tm jazz", "ref", REQUESTED, "isn't a Ticketmaster event ID"),  # a space
        ("x" * 65, "ref", REQUESTED, "isn't a Ticketmaster event ID"),
        ("tm-jazz;drop", "ref", REQUESTED, "isn't a Ticketmaster event ID"),
        (TARGET, "", REQUESTED, "reference must be"),
        (TARGET, "two\nlines", REQUESTED, "reference must be"),
        (TARGET, "r" * 201, REQUESTED, "reference must be"),
        (TARGET, "ref", datetime(2026, 10, 31, 9, 0), "requested_at must"),  # no offset
        (TARGET, "ref", NOW + timedelta(hours=1), "requested_at must"),  # in the future
    ],
)
def test_bad_input_is_refused_before_anything_changes(
    db, sql, stored, source_id, reference, requested_at, why
):
    before = snapshot(sql)
    with pytest.raises(takedown.TakedownError, match=why):
        takedown.run(db, source_id, reference, requested_at, NOW)
    assert snapshot(sql) == before


def test_main_warns_when_past_24_hours_and_fails_on_an_unknown_id(
    db_url, sql, stored, monkeypatch, capsys
):
    monkeypatch.setenv("SUPABASE_DB_URL", db_url)
    late = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    assert (
        takedown.main(["--source-id", TARGET, "--reference", "TM-REQ-9", "--requested-at", late])
        == 0
    )
    out = capsys.readouterr().out
    assert '"status": "removed"' in out and "past the 24 hours" in out
    assert takedown.main(["--source-id", "tm-unknown", "--reference", "TM-REQ-10"]) == 1
    assert "no ticketmaster event" in capsys.readouterr().err


def test_the_browser_roles_cant_read_takedowns(db_url, stored):
    with psycopg.connect(db_url, autocommit=True) as conn:
        for role in ("anon", "authenticated"):
            conn.execute(f"set role {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("select * from takedowns")
            conn.execute("reset role")
