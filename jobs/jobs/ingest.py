"""Ingest events from one source into the events table.

Usage: python -m jobs.ingest {ticketmaster|skiddle} [--dry-run] [--save-sample DIR]

A dry run fetches and maps everything but writes nothing. Its connection is
read-only, so any write fails in Postgres, not just in this code.
"""

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
from pydantic import ValidationError

from jobs import config, dedupe
from jobs.db import Database, RunCounts
from jobs.embeddings import embed
from jobs.geo import haversine_km
from jobs.http import CallBudget, RateLimitedClient
from jobs.normalise import Event, Skipped
from jobs.sources import skiddle, ticketmaster
from jobs.windows import HORIZON, initial_windows

CITY_ID = "london"
RADIUS_KM = 25.0
EMBED_BATCH = 256


@dataclass
class Summary:
    source: str
    dry_run: bool
    counts: RunCounts
    calls: int = 0
    events_in_radius: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    complete: bool = False
    cap_reached: str | None = None
    # Each failure: window, HTTP status (null for a network error or an overflow), message,
    # and the page (Ticketmaster) or offset (Skiddle) it failed on.
    failed_windows: list[dict[str, Any]] = field(default_factory=list)
    split_windows: int = 0
    page_sizes: list[int] = field(default_factory=list)
    # Each window's total and result count; Skiddle windows also list their pages.
    windows: list[dict[str, Any]] = field(default_factory=list)
    swept: bool = False
    embedded: int = 0


def run(
    source: str,
    db: Database,
    http: httpx.Client,
    api_key: str,
    now: datetime,
    dry_run: bool,
    embedder: Callable[[list[str]], list[list[float]]] = embed,
    on_page: Callable[..., None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Summary:
    if dry_run and not db.read_only:
        raise ValueError("a dry run needs a read-only database connection")
    module = ticketmaster if source == "ticketmaster" else skiddle
    city = db.city(CITY_ID)
    today = now.astimezone(UTC).date()
    budget = CallBudget(module.PER_RUN_CAP, module.DAILY_STOP, db.calls_today(source, today))
    client = RateLimitedClient(http, module.MIN_INTERVAL, budget, sleep=sleep)
    summary = Summary(source=source, dry_run=dry_run, counts=RunCounts())
    run_id = None if dry_run else db.start_run(f"ingest_{source}", city.id, now)
    finished = False
    try:
        if source == "ticketmaster":
            windows = initial_windows(now)
        else:
            windows = skiddle.date_windows(now.date(), (now + HORIZON).date())
        result = module.fetch(client, api_key, city.lat, city.lon, RADIUS_KM, windows, on_page)
        summary.failed_windows = [asdict(f) for f in result.failed_windows]
        summary.split_windows = result.split_windows
        summary.page_sizes = sorted(result.page_sizes)
        summary.windows = result.windows
        summary.complete = result.complete
        summary.cap_reached = result.cap_reached
        summary.counts.fetched = len(result.items)

        events, postponed, skipped = _map(result.items, module, city, now, summary.counts)
        summary.skipped = dict(skipped)
        summary.events_in_radius = len(events)

        if not dry_run:
            _write(db, source, city.id, events, postponed, now, summary)
            summary.embedded = _embed_missing(db, embedder)
        finished = True
        return summary
    except BaseException:
        if not dry_run:
            db.conn.rollback()
        raise
    finally:
        summary.calls = budget.used
        if not dry_run:
            # API calls count against the quota whether or not the run finished.
            db.add_calls(source, today, budget.used, module.DAILY_STOP)
            if finished:  # a crashed run keeps duration_ms null (0006)
                db.finish_run(run_id, datetime.now(UTC) - now, summary.counts)


def _map(items, module, city, now, counts) -> tuple[list[Event], list[str], Counter]:
    events: dict[str, Event] = {}
    postponed: list[str] = []
    skipped: Counter = Counter()
    for raw in items:
        try:
            parsed = module.parse(raw, city.tz)
        except (ValidationError, KeyError, ValueError, TypeError):
            counts.failed += 1
            continue
        if isinstance(parsed, Skipped):
            skipped[parsed.reason] += 1
            if parsed.delete and parsed.source_id:
                postponed.append(parsed.source_id)
            continue
        if parsed.has_ended(now):
            skipped["already ended"] += 1
        elif parsed.lat is None or parsed.lon is None:
            skipped["no location"] += 1
        elif haversine_km(city.lat, city.lon, parsed.lat, parsed.lon) > RADIUS_KM:
            skipped[f"outside {RADIUS_KM:g} km"] += 1
        else:
            events[parsed.source_id] = parsed  # an event seen twice keeps its latest copy
    return list(events.values()), postponed, skipped


def _write(db, source, city_id, events, postponed, now, summary) -> None:
    counts = summary.counts
    venue_ids: dict[str, Any] = {}
    with db.conn.transaction():
        for event in events:
            try:
                with db.conn.transaction():  # a savepoint, so one bad row doesn't sink the run
                    venue_id = None
                    if event.venue:
                        key = event.venue.source_id
                        if key not in venue_ids:
                            venue_ids[key] = db.upsert_venue(event.venue, city_id)
                        venue_id = venue_ids[key]
                    is_new = db.upsert_event(event, city_id, venue_id, checked_at=now)
            except psycopg.Error:
                counts.failed += 1
                continue
            if is_new:
                counts.new += 1
            else:
                counts.updated += 1

        counts.deleted += db.delete_events(source, postponed)

        # Only a complete, clean Ticketmaster run can tell us an event has vanished.
        if source == "ticketmaster" and summary.complete and counts.failed == 0:
            counts.deleted += db.sweep_vanished(source, now, now + HORIZON, run_started=now)
            summary.swept = True


def _embed_missing(db: Database, embedder) -> int:
    done = 0
    while rows := db.events_missing_embedding(EMBED_BATCH):
        vectors = embedder([text for _, text in rows])
        with db.conn.transaction():
            for (event_id, _), vector in zip(rows, vectors, strict=True):
                db.set_embedding(event_id, vector)
        done += len(rows)
    return done


def _sample_writer(directory: Path) -> Callable[..., None]:
    directory.mkdir(parents=True, exist_ok=True)

    def write(*key_and_body: Any) -> None:
        *key, body = key_and_body
        name = "-".join(
            k.start.strftime("%Y%m%dT%H%M") if hasattr(k, "start") else str(k) for k in key
        )
        (directory / f"page-{name}.json").write_text(json.dumps(_strip_links(body), indent=1))

    return write


def _strip_links(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_links(v) for k, v in value.items() if k != "_links"}
    if isinstance(value, list):
        return [_strip_links(v) for v in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs.ingest")
    parser.add_argument("source", choices=["ticketmaster", "skiddle"])
    parser.add_argument("--dry-run", action="store_true", help="fetch and map, write nothing")
    parser.add_argument("--save-sample", type=Path, help="save raw pages here (no _links)")
    args = parser.parse_args(argv)

    settings = config.from_env()
    api_key = settings.ticketmaster_key if args.source == "ticketmaster" else settings.skiddle_key
    if not api_key:
        print(f"no API key for {args.source}", file=sys.stderr)
        return 2

    db = Database.connect(settings.db_url, read_only=args.dry_run)
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        with httpx.Client(timeout=30) as http:
            summary = run(
                args.source,
                db,
                http,
                api_key,
                now=now,
                dry_run=args.dry_run,
                on_page=_sample_writer(args.save_sample) if args.save_sample else None,
            )
        print(json.dumps(asdict(summary), indent=2, default=str))
        # After the ingest run has recorded its job_runs row, so a failed dedupe can't hide
        # it. Same connection and mode: a dry ingest gives a read-only, dry dedupe, which
        # sees only what is already stored (the dry ingest wrote nothing).
        links = dedupe.run(db, now, dry_run=args.dry_run)
        print(json.dumps({"dedupe": asdict(links)}, indent=2, default=str))
    finally:
        db.close()

    if summary.cap_reached:
        print(f"::warning::{args.source}: stopped at the {summary.cap_reached}")
    return 1 if summary.failed_windows or summary.counts.failed else 0


if __name__ == "__main__":
    sys.exit(main())
