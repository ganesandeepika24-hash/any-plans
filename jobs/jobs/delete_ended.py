"""Delete ended events, their price snapshots and orphaned Ticketmaster venues (0007).

How long after its end an event is kept is config (`event_retention`, migration 025):
days per source, with a default. Every source is 0 today, decision 0007's rule. If the
config is missing or malformed, every source uses 0: a bad read never keeps data longer.

Usage: python -m jobs.delete_ended
"""

import json
import sys
from datetime import UTC, datetime, timedelta

from jobs import config
from jobs.db import Database, RunCounts
from jobs.normalise import FALLBACK_DURATION

JOB = "delete_ended_events"
RETENTION_KEY = "event_retention"
MAX_DAYS = 7  # a short grace at most: 0001 allows no archive


def retention_days(value: object) -> tuple[int, dict[str, int]] | None:
    """(default days, days per source) from the config value, or None if it's malformed."""
    if not isinstance(value, dict) or set(value) != {"default_days_after_end", "sources"}:
        return None
    default, sources = value["default_days_after_end"], value["sources"]
    if not isinstance(sources, dict):
        return None
    days = [default, *sources.values()]
    if any(type(d) is not int or not 0 <= d <= MAX_DAYS for d in days):
        return None
    if any(not isinstance(s, str) or not s for s in sources):
        return None
    return default, dict(sources)


def run(db: Database, now: datetime) -> RunCounts:
    counts = RunCounts()
    run_id = db.start_run(JOB, None, now)  # spans every city
    parsed = retention_days(db.config_value(RETENTION_KEY))
    if parsed is None:
        print(f"::warning::{RETENTION_KEY} is missing or malformed: every source uses 0 days")
        parsed = (0, {})
    default, per_source = parsed
    cutoffs = {source: now - timedelta(days=d) for source, d in per_source.items()}
    with db.conn.transaction():
        events, snapshots = db.delete_ended_events(
            now - timedelta(days=default), FALLBACK_DURATION, cutoffs
        )
        venues = db.delete_orphan_venues("ticketmaster")
    counts.deleted = events + venues + snapshots
    db.finish_run(run_id, datetime.now(UTC) - now, counts)
    print(
        json.dumps(
            {
                "events_deleted": events,
                "venues_deleted": venues,
                "snapshots_deleted": snapshots,
                "retention_days": {"default": default, **per_source},
            }
        )
    )
    return counts


def main() -> int:
    db = Database.connect(config.from_env().db_url, read_only=False)
    try:
        run(db, datetime.now(UTC).replace(microsecond=0))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
