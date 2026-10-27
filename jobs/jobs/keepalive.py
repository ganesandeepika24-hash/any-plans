"""Keep the Supabase project from pausing (decision 0011).

Usage: python -m jobs.keepalive

Free projects pause after a week without database activity. Every three days this
records a job_runs row and counts the events users can see: real queries in real
transactions, not just a connection. `fetched` is that count.
"""

import json
import sys
from datetime import UTC, datetime

from jobs import config
from jobs.db import Database, RunCounts

JOB = "keepalive"


def run(db: Database, now: datetime) -> RunCounts:
    counts = RunCounts()
    run_id = db.start_run(JOB, None, now)  # spans every city
    counts.fetched = db.visible_event_count()
    db.finish_run(run_id, datetime.now(UTC) - now, counts)
    print(json.dumps({"job": JOB, "visible_events": counts.fetched}))
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
