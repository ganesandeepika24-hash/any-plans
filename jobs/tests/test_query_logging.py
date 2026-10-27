"""queries and query_results (migration 002, decision 0013). Needs TEST_DATABASE_URL."""

import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.db

USER = "0f8fad5b-d9cb-469f-a165-70867728950e"


def agent_row(**overrides) -> dict:
    """A row shaped like the agent's /parse row (agent/app/parse.py)."""
    row = {
        "id": str(uuid.uuid4()),
        "user_id": USER,
        "city_id": "london",
        "route": "parse",
        "query_text": "jazz in soho tonight",
        "status": "ok",
        "provider": "groq",
        "attempts": [{"provider": "groq", "model": "openai/gpt-oss-20b", "outcome": "ok"}],
        "filters": {"categories": ["Music"]},
        "embedding_text": "jazz",
        "notes": [],
        "config_versions": {"llm_understand": 1, "understand_prompt": 1, "ai_search_cap": 1},
        "release": "dev",
        "trace_id": "a" * 32,
        "latency_ms": 840,
    }
    return row | overrides


@pytest.fixture
def service(db_url, db):
    """A connection acting as the agent's service_role. `db` empties the tables first."""
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("delete from public.queries")
        conn.execute("set role service_role")
        yield conn


def insert(conn, row: dict) -> None:
    columns = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    values = [Jsonb(v) if isinstance(v, dict | list) else v for v in row.values()]
    conn.execute(f"insert into public.queries ({columns}) values ({marks})", values)  # noqa: S608


def test_service_role_writes_the_agents_row(service):
    row = agent_row()
    insert(service, row)
    got = service.execute(
        "select status, attempts, filters, created_at is not null"
        " from public.queries where id = %s",
        (row["id"],),
    ).fetchone()
    assert got == ("ok", row["attempts"], row["filters"], True)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "capped", "provider": None, "filters": None, "trace_id": None, "attempts": []},
        {"status": "ai_resting", "provider": None, "filters": None},
    ],
    ids=["capped", "resting"],
)
def test_capped_and_resting_rows_have_no_filters(service, overrides):
    insert(service, agent_row(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "ok", "filters": None},
        {"status": "ai_resting"},  # resting with filters
        {"status": "pending"},
        {"route": "search"},
        {"query_text": ""},
        {"query_text": "x" * 501},
        {"trace_id": "not-a-trace"},
        {"latency_ms": -1},
        {"city_id": "atlantis"},
        {"user_id": None},
    ],
    ids=[
        "ok without filters",
        "resting with filters",
        "unknown status",
        "unknown route",
        "empty query",
        "query too long",
        "bad trace id",
        "negative latency",
        "unknown city",
        "no user",
    ],
)
def test_bad_rows_are_refused(service, overrides):
    with pytest.raises(
        (
            psycopg.errors.CheckViolation,
            psycopg.errors.ForeignKeyViolation,
            psycopg.errors.NotNullViolation,
        )
    ):
        insert(service, agent_row(**overrides))


def test_query_results_cascade_and_hold_no_event_details(service):
    row = agent_row()
    insert(service, row)
    service.execute(
        "insert into public.query_results (query_id, rank, source, source_id, score)"
        " values (%s, 1, 'skiddle', '42262752', 0.82)",
        (row["id"],),
    )
    columns = {
        r[0]
        for r in service.execute(
            "select column_name from information_schema.columns"
            " where table_schema = 'public' and table_name = 'query_results'"
        )
    }
    assert columns == {"query_id", "rank", "source", "source_id", "score"}
    service.execute("delete from public.queries where id = %s", (row["id"],))
    assert service.execute("select count(*) from public.query_results").fetchone() == (0,)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_anon_and_authenticated_can_neither_read_nor_write(db_url, service, role):
    insert(service, agent_row())
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(f"set role {role}")
        for statement in (
            "select * from public.queries",
            "select * from public.query_results",
            "insert into public.queries (id, user_id, city_id, route, query_text, status)"
            " values (gen_random_uuid(), '0f8fad5b-d9cb-469f-a165-70867728950e', 'london',"
            " 'parse', 'q', 'capped')",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_the_cap_count_matches_what_the_agent_asks_for(service):
    # The agent counts ok and ai_resting rows since midnight UTC (agent/app/querylog.py).
    for status in ("ok", "ai_resting", "capped"):
        overrides = {"status": status}
        if status != "ok":
            overrides |= {"filters": None, "provider": None}
        insert(service, agent_row(**overrides))
    insert(service, agent_row(user_id=str(uuid.uuid4())))
    count = service.execute(
        "select count(*) from public.queries where user_id = %s"
        " and created_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc'"
        " and status in ('ok', 'ai_resting')",
        (USER,),
    ).fetchone()[0]
    assert count == 2


def test_the_cap_config_is_active(sql):
    assert sql("select value_json from config where key = 'ai_search_cap' and is_active") == [
        ({"per_user_per_day": 10},)
    ]
