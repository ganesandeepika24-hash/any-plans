"""The event embedding job (step 1.2d, decision 0009).

Unit tests need nothing. DB tests need TEST_DATABASE_URL. All use FakeEmbedder; the
autouse no_model_download fixture fails any test that would load the real model.
"""

import copy
import re

import httpx
import psycopg
import pytest

from jobs import embed_events, embeddings, ingest
from jobs.db import Database
from jobs.normalise import DESCRIPTION_LIMIT, Event, clean_description
from jobs.sources import skiddle, ticketmaster
from tests.conftest import LONDON, NOW, FakeEmbedder, fixture
from tests.test_db_integration import run_tm, tm_server

# Text and hash -------------------------------------------------------------------


def test_text_is_title_category_genre_venue_description_in_that_order():
    text = embed_events.embedding_text("Late Jazz", "Music", "jazz", "Ronnie's", "Two sets.")
    assert text == "Late Jazz · Music · jazz · Ronnie's · Two sets."


def test_text_skips_empty_and_blank_fields():
    assert embed_events.embedding_text(" Quiz ", "Nightlife", None, "  ", "") == "Quiz · Nightlife"


def test_hash_is_sha256_hex_and_changes_with_text_and_model():
    digest = embed_events.text_hash("Quiz · Nightlife")
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == embed_events.text_hash("Quiz · Nightlife")
    assert digest != embed_events.text_hash("Quiz · Nightlife ")
    assert digest != embed_events.text_hash("Quiz · Nightlife", model="another/model")


def test_the_job_uses_the_shared_model():
    # test_embeddings_model.py checks jobs.embeddings against the agent's.
    assert embed_events.MODEL_NAME == embeddings.MODEL_NAME
    assert embed_events.DIMENSIONS == embeddings.DIMENSIONS == 384


def test_tests_cannot_load_the_real_model():
    with pytest.raises(AssertionError, match="fake embedder"):
        embeddings.embed(["anything"])


# Descriptions ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<p>Two <b>sets</b>,&nbsp;late&amp;loud.</p>", "Two sets , late&loud."),
        ("  line one\n\n\tline two  ", "line one line two"),
        ("<br/><p> </p>", None),
        ("", None),
        (None, None),
    ],
    ids=["html", "whitespace", "only tags", "empty", "none"],
)
def test_clean_description(raw, expected):
    assert clean_description(raw) == expected


def test_clean_description_cuts_long_text_at_a_word_boundary():
    text = clean_description("word " * 400)
    assert len(text) <= DESCRIPTION_LIMIT
    assert text.endswith("word") and text == text.strip()


def test_clean_description_cuts_one_long_word_at_the_limit():
    assert clean_description("x" * 5000) == "x" * DESCRIPTION_LIMIT


def tm_item() -> dict:
    return copy.deepcopy(fixture("ticketmaster/events-page.json")["_embedded"]["events"][0])


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"info": "<p>Doors 7pm.</p>", "description": "A night of jazz."}, "Doors 7pm."),
        ({"info": "  ", "description": "A night of jazz."}, "A night of jazz."),
        ({"pleaseNote": "No re-entry.", "additionalInfo": "Bag policy."}, None),
        ({}, None),
    ],
    ids=["info first", "description fallback", "logistics ignored", "none"],
)
def test_ticketmaster_description(fields, expected):
    event = ticketmaster.parse(tm_item() | fields, LONDON.tz)
    assert isinstance(event, Event)
    assert event.description == expected


def test_skiddle_description_is_not_mapped():
    # Skiddle's docs name no response field for event text (0009).
    raws = copy.deepcopy(fixture("skiddle/events-page.json")["results"])
    mapped = [skiddle.parse(r | {"description": "Guessed field"}, LONDON.tz) for r in raws]
    events = [e for e in mapped if isinstance(e, Event)]
    assert events
    assert all(e.description is None for e in events)


# The job (database) ----------------------------------------------------------------


def add_venue(sql, source_id: str, name: str) -> str:
    return sql(
        "insert into venues (city_id, source, source_id, name, kind)"
        " values ('london', 'test', %s, %s, 'other') returning id",
        (source_id, name),
    )[0][0]


def add_event(sql, source_id: str, title: str, **columns) -> str:
    row = {"category": "Music", "start_at": NOW} | columns
    names = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    return sql(
        f"insert into events (city_id, source, source_id, title, {names})"  # noqa: S608
        f" values ('london', 'test', %s, %s, {marks}) returning id",
        (source_id, title, *row.values()),
    )[0][0]


@pytest.fixture
def events(db, sql):
    """Three events users can see, plus a cancelled one and a duplicate."""
    venue = add_venue(sql, "v1", "Ronnie's")
    ids = {
        "jazz": add_event(
            sql, "jazz", "Late Jazz", genre="jazz", venue_id=venue, description="Two sets."
        ),
        "quiz": add_event(sql, "quiz", "Quiz", category="Nightlife"),
        "play": add_event(sql, "play", "Hamlet", category="Theatre"),
        "cancelled": add_event(sql, "cancelled", "Off", status="cancelled"),
    }
    ids["duplicate"] = add_event(sql, "duplicate", "Quiz night", duplicate_of=ids["quiz"])
    return ids


TEXTS = ["Hamlet · Theatre", "Late Jazz · Music · jazz · Ronnie's · Two sets.", "Quiz · Nightlife"]


def embed(db, embedder=None, **kwargs):
    return embed_events.run(db, NOW, dry_run=False, embedder=embedder or FakeEmbedder(), **kwargs)


def embedded(sql) -> dict[str, bool]:
    rows = sql("select source_id, embedding is not null and embedding_hash is not null from events")
    return dict(rows)


@pytest.mark.db
def test_first_run_embeds_visible_events_and_logs_a_job_run(db, sql, events):
    embedder = FakeEmbedder()
    summary = embed(db, embedder)

    assert sorted(embedder.texts) == TEXTS
    assert (summary.counts.fetched, summary.counts.new, summary.counts.updated) == (3, 3, 0)
    assert embedded(sql) == {
        "jazz": True,
        "quiz": True,
        "play": True,
        "cancelled": False,
        "duplicate": False,
    }
    (digest,) = sql("select embedding_hash from events where source_id = 'quiz'")[0]
    assert digest == embed_events.text_hash("Quiz · Nightlife")
    assert sql(
        "select job, city_id, duration_ms is not null, fetched, new, updated, failed, deleted"
        " from job_runs"
    ) == [("embed_events", None, True, 3, 3, 0, 0, 0)]


@pytest.mark.db
def test_unchanged_events_never_reach_the_embedder(db, sql, events):
    embed(db)
    embedder = FakeEmbedder()
    summary = embed(db, embedder)
    assert embedder.calls == []
    assert (summary.counts.fetched, summary.counts.new, summary.counts.updated) == (3, 0, 0)
    assert summary.unchanged == 3
    assert sql("select fetched, new, updated from job_runs order by id") == [
        (3, 3, 0),
        (3, 0, 0),
    ]


@pytest.mark.parametrize(
    ("change", "text"),
    [
        (
            "update events set title = 'Later Jazz' where source_id = 'jazz'",
            "Later Jazz · Music · jazz · Ronnie's · Two sets.",
        ),
        (
            "update events set category = 'Nightlife' where source_id = 'jazz'",
            "Late Jazz · Nightlife · jazz · Ronnie's · Two sets.",
        ),
        (
            "update events set genre = 'blues' where source_id = 'jazz'",
            "Late Jazz · Music · blues · Ronnie's · Two sets.",
        ),
        (
            "update events set description = 'One set.' where source_id = 'jazz'",
            "Late Jazz · Music · jazz · Ronnie's · One set.",
        ),
        (
            "update venues set name = 'Ronnie Scott''s' where source_id = 'v1'",
            "Late Jazz · Music · jazz · Ronnie Scott's · Two sets.",
        ),
    ],
    ids=["title", "category", "genre", "description", "venue renamed"],
)
@pytest.mark.db
def test_a_changed_field_re_embeds_only_that_event(db, sql, events, change, text):
    embed(db)
    sql(change)
    embedder = FakeEmbedder()
    summary = embed(db, embedder)
    assert embedder.texts == [text]
    assert (summary.counts.new, summary.counts.updated, summary.unchanged) == (0, 1, 2)


@pytest.mark.db
def test_vectors_without_a_hash_are_re_embedded_as_new(db, sql, events):
    # Vectors written before migration 009 have no hash.
    vector = "[" + ",".join(["0.5"] * 384) + "]"
    sql("update events set embedding = %s::extensions.vector", (vector,))
    summary = embed(db)
    assert (summary.counts.new, summary.counts.updated) == (3, 0)
    assert sql("select count(*) from events where embedding_hash is not null") == [(3,)]


@pytest.mark.db
def test_a_model_change_re_embeds_everything(db, sql, events):
    embed(db)
    for source_id, text in [("quiz", "Quiz · Nightlife"), ("play", "Hamlet · Theatre")]:
        sql(
            "update events set embedding_hash = %s where source_id = %s",
            (embed_events.text_hash(text, model="older/model"), source_id),
        )
    embedder = FakeEmbedder()
    summary = embed(db, embedder)
    assert sorted(embedder.texts) == ["Hamlet · Theatre", "Quiz · Nightlife"]
    assert (summary.counts.new, summary.counts.updated) == (0, 2)


@pytest.mark.db
def test_an_event_that_stops_being_a_duplicate_is_embedded(db, sql, events):
    embed(db)
    sql("update events set duplicate_of = null where source_id = 'duplicate'")
    embedder = FakeEmbedder()
    embed(db, embedder)
    assert embedder.texts == ["Quiz night · Music"]


@pytest.mark.db
def test_each_batch_commits_so_a_failure_keeps_earlier_batches(db, sql, events):
    with pytest.raises(RuntimeError, match="embedding failed"):
        embed(db, FakeEmbedder(fail_after=1), batch_size=2)
    assert sql("select count(*) from events where embedding_hash is not null") == [(2,)]
    assert sql("select job, duration_ms from job_runs") == [("embed_events", None)]

    summary = embed(db)  # the next run picks up the rest
    assert (summary.counts.new, summary.unchanged) == (1, 2)


@pytest.mark.db
def test_a_vector_of_the_wrong_length_is_refused(db, sql, events):
    with pytest.raises(ValueError, match="expected 384 dimensions, got 3"):
        embed(db, FakeEmbedder(dimensions=3))
    assert sql("select count(*) from events where embedding is not null") == [(0,)]


@pytest.mark.db
def test_dry_run_counts_writes_nothing_and_never_loads_the_model(db_url, sql, events):
    read_only = Database.connect(db_url, read_only=True)
    try:
        # The default embedder: loading the model would fail (no_model_download).
        summary = embed_events.run(read_only, NOW, dry_run=True)
    finally:
        read_only.close()
    assert (summary.counts.fetched, summary.counts.new, summary.counts.updated) == (3, 3, 0)
    assert sorted(summary.samples) == TEXTS
    assert sql("select count(*) from events where embedding is not null") == [(0,)]
    assert sql("select count(*) from job_runs") == [(0,)]


@pytest.mark.db
def test_dry_run_refuses_a_writable_connection(db, events):
    with pytest.raises(ValueError, match="read-only"):
        embed_events.run(db, NOW, dry_run=True, embedder=FakeEmbedder())


@pytest.mark.db
def test_a_read_only_connection_cannot_write_embeddings(db_url, events):
    read_only = Database.connect(db_url, read_only=True)
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            read_only.set_embeddings([(events["quiz"], [0.1] * 384, "a" * 64)])
    finally:
        read_only.close()


@pytest.mark.db
def test_ingest_stores_the_description_and_keeps_the_vector_on_reingest(db, sql):
    page = fixture("ticketmaster/events-page.json")
    page["_embedded"]["events"][0]["info"] = "<p>Two sets.</p>"
    run_tm(db, tm_server(page))
    embed(db)
    before = sql("select embedding_hash from events where description = 'Two sets.'")
    assert len(before) == 1

    run_tm(db, tm_server(page))  # same data again
    embedder = FakeEmbedder()
    embed(db, embedder)
    assert embedder.calls == []
    assert sql("select embedding_hash from events where description = 'Two sets.'") == before


def run_skiddle(db):
    page = fixture("skiddle/events-page.json")  # trimmed real events
    return ingest.run(
        "skiddle",
        db,
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=page))),
        "KEY",
        now=NOW,
        dry_run=False,
        sleep=lambda s: None,
    )


@pytest.mark.db
def test_a_nightly_skiddle_reingest_re_embeds_nothing(db, sql):
    # The nightly run upserts every Skiddle event again. Their text hasn't changed, so
    # none of them reaches the embedder and no vector is rewritten.
    run_skiddle(db)
    visible = embed(db).counts.new
    assert visible > 0
    before = sql("select id, embedding_hash from events where source = 'skiddle' order by id")

    run_skiddle(db)
    embedder = FakeEmbedder()
    summary = embed(db, embedder)
    assert embedder.calls == []
    assert (summary.counts.new, summary.counts.updated, summary.unchanged) == (0, 0, visible)
    assert sql("select id, embedding_hash from events where source = 'skiddle' order by id") == (
        before
    )


@pytest.mark.db
def test_each_batch_is_one_write(db, sql, events, monkeypatch):
    # One round trip per batch, not per row: per-row updates over the pooler made the
    # re-embed after 009 outlast the 30-minute workflow limit.
    writes = []
    set_embeddings = db.set_embeddings

    def counted(rows):
        writes.append(len(rows))
        return set_embeddings(rows)

    monkeypatch.setattr(db, "set_embeddings", counted)
    embed(db, batch_size=2)
    assert writes == [2, 1]
    assert sql("select count(*) from events where embedding_hash is not null") == [(3,)]


@pytest.mark.db
def test_set_embeddings_writes_each_rows_own_vector_and_hash(db, sql, events):
    rows = [
        (events["quiz"], [0.25] * 384, "a" * 64),
        (events["play"], [-0.5] * 384, "b" * 64),
    ]
    with db.conn.transaction():
        assert db.set_embeddings(rows) == 2
    assert db.set_embeddings([]) == 0
    stored = sql(
        "select source_id, (embedding::real[])[1], (embedding::real[])[384], embedding_hash"
        " from events where embedding is not null order by source_id"
    )
    assert stored == [("play", -0.5, -0.5, "b" * 64), ("quiz", 0.25, 0.25, "a" * 64)]
