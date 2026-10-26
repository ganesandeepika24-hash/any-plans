"""Daily price snapshots: one row per current, priced event per local day (decision 0009).

Usage: python -m jobs.snapshot_prices [--dry-run]

The day is each city's local date, computed in Python from the city's timezone, not
from the database clock. A dry run uses a read-only connection and writes nothing.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from jobs import config
from jobs.db import Database, RunCounts
from jobs.normalise import FALLBACK_DURATION

JOB = "snapshot_prices"


@dataclass
class Summary:
    dry_run: bool
    counts: RunCounts
    days: dict[str, str] = field(default_factory=dict)  # city -> local date snapshotted


def run(db: Database, now: datetime, dry_run: bool) -> Summary:
    if dry_run and not db.read_only:
        raise ValueError("a dry run needs a read-only database connection")
    summary = Summary(dry_run=dry_run, counts=RunCounts())
    started = time.monotonic()  # duration from a real clock: `now` may be fixed in tests
    counts = summary.counts
    run_id = None if dry_run else db.start_run(JOB, None, now)  # spans every city
    finished = False
    try:
        cities = db.snapshot_cities(now, FALLBACK_DURATION)
        for city_id, tz in cities:
            day = now.astimezone(tz).date()
            summary.days[city_id] = day.isoformat()
            if dry_run:
                total, existing = db.count_snapshot_targets(city_id, day, now, FALLBACK_DURATION)
                counts.fetched += total
                counts.new += total - existing
                counts.updated += existing
                continue
            with db.conn.transaction():
                inserted, updated = db.snapshot_prices(city_id, day, now, FALLBACK_DURATION)
            counts.fetched += inserted + updated
            counts.new += inserted
            counts.updated += updated
        if dry_run:
            db.conn.rollback()
        finished = True
        return summary
    except BaseException:
        db.conn.rollback()
        raise
    finally:
        if not dry_run and finished:  # a crashed run keeps duration_ms null (0006)
            db.finish_run(run_id, timedelta(seconds=time.monotonic() - started), counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs.snapshot_prices")
    parser.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    args = parser.parse_args(argv)

    db = Database.connect(config.from_env().db_url, read_only=args.dry_run)
    try:
        summary = run(db, datetime.now(UTC).replace(microsecond=0), dry_run=args.dry_run)
    finally:
        db.close()
    print(json.dumps(asdict(summary), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
