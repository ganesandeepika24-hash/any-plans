"""Retention in config (migration 025, decisions 0001 and 0007): days after the end, per
source, before the nightly delete removes an event. Ticketmaster is 0 (owner's choice
(a), 29 Oct 2026), the same as every other source, so today nothing changes. These
tests check that the days come from config and apply only to their own source."""

from datetime import timedelta

import psycopg
import pytest

from jobs import delete_ended
from tests.conftest import NOW

pytestmark = pytest.mark.db

HOUR = timedelta(hours=1)


@pytest.fixture(autouse=True)
def retention_v1(db_url):
    """Each test starts and ends on 025's event_retention (version 1)."""

    def reset():
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute("select public.set_active_config('event_retention', 1)")
            conn.execute("delete from public.config where key = 'event_retention' and version > 1")

    reset()
    yield
    reset()


def use(sql, value):
    version = sql("select max(version) + 1 from config where key = 'event_retention'")[0][0]
    sql(
        "insert into config (key, version, value_json, updated_by, is_active)"
        " values ('event_retention', %s, %s, 'test', false)",
        (version, psycopg.types.json.Jsonb(value)),
    )
    sql("select set_active_config('event_retention', %s)", (version,))


@pytest.fixture
def ended(db, sql):
    """Events from three sources, ended 1 hour and 30 hours before NOW, one with no end
    (started 4 hours ago, so ended under the 3-hour rule), and one still to come."""
    rows = []
    for source in ("ticketmaster", "skiddle", "test"):
        for name, start, end in (
            ("1h", NOW - 5 * HOUR, NOW - HOUR),
            ("30h", NOW - 34 * HOUR, NOW - 30 * HOUR),
            ("no end", NOW - 4 * HOUR, None),
            ("future", NOW + 5 * HOUR, None),
        ):
            rows.append((source, f"{source} {name}"))
            sql(
                "insert into events (city_id, source, source_id, title, category, start_at,"
                " end_at) values ('london', %s, %s, %s, 'Music', %s, %s)",
                (source, f"{source} {name}", name, start, end),
            )
    return rows


def kept(sql) -> set[str]:
    return {r[0] for r in sql("select source_id from events")}


def test_the_shipped_config_deletes_every_source_at_its_end(db, sql, ended):
    delete_ended.run(db, NOW)
    assert kept(sql) == {"ticketmaster future", "skiddle future", "test future"}


def test_ticketmasters_days_apply_to_ticketmaster_only(db, sql, ended):
    use(sql, {"default_days_after_end": 0, "sources": {"ticketmaster": 1}})
    delete_ended.run(db, NOW)
    assert kept(sql) == {
        "ticketmaster 1h",  # within a day of its end
        "ticketmaster no end",
        "ticketmaster future",
        "skiddle future",
        "test future",
    }


def test_the_default_applies_to_sources_not_listed(db, sql, ended):
    use(sql, {"default_days_after_end": 2, "sources": {"ticketmaster": 0}})
    delete_ended.run(db, NOW)
    assert kept(sql) == {
        "ticketmaster future",
        *(f"{s} {n}" for s in ("skiddle", "test") for n in ("1h", "30h", "no end", "future")),
    }


@pytest.mark.parametrize(
    "value",
    [
        {"sources": {}},
        {"default_days_after_end": 0},
        {"default_days_after_end": "1", "sources": {}},
        {"default_days_after_end": 1.5, "sources": {}},
        {"default_days_after_end": -1, "sources": {}},
        {"default_days_after_end": 8, "sources": {}},  # over MAX_DAYS
        {"default_days_after_end": 0, "sources": {"ticketmaster": True}},
        {"default_days_after_end": 0, "sources": []},
        {"default_days_after_end": 0, "sources": {"": 1}},
        {"default_days_after_end": 0, "sources": {}, "extra": 1},
        [0],
    ],
)
def test_a_malformed_config_uses_0_days_for_every_source(db, sql, ended, value, capsys):
    use(sql, {"default_days_after_end": 3, "sources": {"ticketmaster": 3}})
    use(sql, value)
    delete_ended.run(db, NOW)
    assert kept(sql) == {"ticketmaster future", "skiddle future", "test future"}
    assert "every source uses 0 days" in capsys.readouterr().out


def test_no_active_config_uses_0_days(db, sql, ended, capsys):
    sql("update config set is_active = false where key = 'event_retention'")
    delete_ended.run(db, NOW)
    assert kept(sql) == {"ticketmaster future", "skiddle future", "test future"}
    assert "every source uses 0 days" in capsys.readouterr().out


def test_snapshots_go_with_their_event_and_others_stay(db, sql, ended):
    use(sql, {"default_days_after_end": 0, "sources": {"ticketmaster": 1}})
    for source_id in ("ticketmaster 1h", "skiddle 1h"):
        sql(
            "insert into price_snapshots (event_id, snapshot_date, status)"
            " select id, date '2026-10-31', 'active' from events where source_id = %s",
            (source_id,),
        )
    counts = delete_ended.run(db, NOW)
    remaining = sql("select e.source_id from price_snapshots p join events e on e.id = p.event_id")
    assert remaining == [("ticketmaster 1h",)]
    # 7 events (Ticketmaster's 30h one, three from each other source) and 1 snapshot.
    assert counts.deleted == 7 + 1


@pytest.mark.parametrize(
    ("value", "parsed"),
    [
        ({"default_days_after_end": 0, "sources": {"ticketmaster": 0}}, (0, {"ticketmaster": 0})),
        ({"default_days_after_end": 7, "sources": {}}, (7, {})),
        ({"default_days_after_end": 0, "sources": {"a": 1, "b": 7}}, (0, {"a": 1, "b": 7})),
        (None, None),
        ({"default_days_after_end": True, "sources": {}}, None),  # a bool isn't a number
    ],
)
def test_retention_days_parsing(value, parsed):
    assert delete_ended.retention_days(value) == parsed
