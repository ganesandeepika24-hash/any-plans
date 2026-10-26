from jobs import ingest
from jobs.db import RunCounts
from jobs.sources import skiddle, ticketmaster
from tests.conftest import LONDON, NOW, fixture


def test_ticketmaster_items_are_filtered_and_counted():
    items = fixture("ticketmaster/events-page.json")["_embedded"]["events"]
    counts = RunCounts()
    events, postponed, skipped = ingest._map(items, ticketmaster, LONDON, NOW, counts)
    assert sorted(e.source_id for e in events) == [
        "tm-cancelled",
        "tm-inverted-price",
        "tm-jazz",
        "tm-multi-day",
        "tm-no-price",
        "tm-no-time",
    ]
    assert postponed == ["tm-postponed"]
    assert skipped == {
        "postponed": 1,
        "unmapped category (Undefined / Undefined)": 1,
        "outside 25 km": 1,
        "already ended": 1,
    }
    assert counts.failed == 0


def test_skiddle_items_are_filtered_and_counted():
    items = fixture("skiddle/events-page.json")["results"]  # 40 trimmed real events
    events, postponed, skipped = ingest._map(items, skiddle, LONDON, NOW, RunCounts())
    assert len(events) == 26
    assert postponed == []
    assert skipped == {
        "skipped event code (TOUR)": 2,
        "skipped event code (empty)": 2,
        **{
            f"skipped event code ({c})": 1
            for c in ("AWARD", "CONF", "EDU", "EXPER", "FUND", "HEALTH", "HOBBY", "NET", "RELIG")
        },
        "outside 25 km": 1,
    }


def test_a_malformed_item_counts_as_failed():
    counts = RunCounts()
    ingest._map(
        [{"id": "broken", "dates": {"start": {"localDate": "not a date"}}}],
        ticketmaster,
        LONDON,
        NOW,
        counts,
    )
    assert counts.failed == 1


def test_an_event_seen_twice_is_written_once():
    item = fixture("ticketmaster/events-page.json")["_embedded"]["events"][0]
    events, _, _ = ingest._map([item, item], ticketmaster, LONDON, NOW, RunCounts())
    assert len(events) == 1


def test_samples_drop_links():
    assert ingest._strip_links({"_links": {"self": 1}, "a": [{"_links": 2, "b": 3}]}) == {
        "a": [{"b": 3}]
    }


def test_the_dry_run_flag_opens_a_read_only_connection(monkeypatch):
    opened = []

    class StopAfterConnect(Exception):
        pass

    def connect(url, read_only):
        opened.append(read_only)
        raise StopAfterConnect

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://example")
    monkeypatch.setenv("TICKETMASTER_KEY", "KEY")
    monkeypatch.setattr(ingest.Database, "connect", staticmethod(connect))
    for argv in (["ticketmaster", "--dry-run"], ["ticketmaster"]):
        try:
            ingest.main(argv)
        except StopAfterConnect:
            pass
    assert opened == [True, False]
