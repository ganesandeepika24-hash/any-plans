"""The keep-alive job (decision 0011). Needs TEST_DATABASE_URL."""

import psycopg
import pytest

from jobs import keepalive
from tests.conftest import NOW

pytestmark = pytest.mark.db


def test_records_a_job_run_with_the_visible_event_count(db, sql):
    sql(
        """
        insert into events (city_id, source, source_id, title, category, start_at, status)
        values ('london', 'test', 'a', 'A', 'Music', now() + interval '1 day', 'active'),
               ('london', 'test', 'b', 'B', 'Music', now() + interval '1 day', 'cancelled'),
               ('london', 'test', 'c', 'C', 'Music', now() - interval '1 day', 'active')
        """
    )
    counts = keepalive.run(db, NOW)
    assert counts.fetched == 1
    assert sql(
        "select job, city_id, duration_ms is not null, fetched, new, updated, failed, deleted"
        " from job_runs"
    ) == [("keepalive", None, True, 1, 0, 0, 0, 0)]


def test_visible_count_matches_what_anon_sees(db, sql, db_url):
    kept = sql(
        "insert into events (city_id, source, source_id, title, category, start_at)"
        " values ('london', 'test', 'k', 'K', 'Music', now() + interval '1 day') returning id"
    )[0][0]
    sql(
        "insert into events (city_id, source, source_id, title, category, start_at, duplicate_of)"
        " values ('london', 'test', 'd', 'D', 'Music', now() + interval '1 day', %s)",
        (kept,),
    )
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("set role anon")
        anon = conn.execute("select count(*) from events").fetchone()[0]
    assert db.visible_event_count() == anon == 1


def test_main_uses_a_writable_connection(monkeypatch):
    opened = []

    class Stop(Exception):
        pass

    def connect(url, read_only):
        opened.append(read_only)
        raise Stop

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://example")
    monkeypatch.setattr(keepalive.Database, "connect", staticmethod(connect))
    with pytest.raises(Stop):
        keepalive.main()
    assert opened == [False]
