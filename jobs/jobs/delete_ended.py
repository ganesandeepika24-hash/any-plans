"""Delete ended events and orphaned Ticketmaster venues (decision 0007).

Usage: python -m jobs.delete_ended
"""

import json
import sys
from datetime import UTC, datetime

from jobs import config
from jobs.db import Database, RunCounts
from jobs.normalise import FALLBACK_DURATION

JOB = "delete_ended_events"


def run(db: Database, now: datetime) -> RunCounts:
    counts = RunCounts()
    run_id = db.start_run(JOB, None, now)  # spans every city
    with db.conn.transaction():
        events = db.delete_ended_events(now, FALLBACK_DURATION)
        venues = db.delete_orphan_venues("ticketmaster")
    counts.deleted = events + venues
    db.finish_run(run_id, datetime.now(UTC) - now, counts)
    print(json.dumps({"events_deleted": events, "venues_deleted": venues}))
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
