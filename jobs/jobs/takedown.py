"""Take down one Ticketmaster event on its owner's request (decision 0001: within 24
hours; runbook docs/runbooks/takedown.md).

In one transaction: record the takedown, delete the event (matched on source and source
ID together), its price snapshots, and its venue if no other event uses it, and show
again any other source's copy that dedupe had hidden behind it. The takedowns row then
stops any later ingest from bringing the event back (migration 025's trigger).

Usage: python -m jobs.takedown --source-id ID --reference REF [--requested-at ISO]
                               [--block-only] [--dry-run]
"""

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from jobs import config
from jobs.db import Database, RunCounts

JOB = "takedown"
SOURCE = "ticketmaster"  # the only source with a takedown condition (0001)
SOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # Ticketmaster event IDs, such as G5vYZ9...
REFERENCE = re.compile(r"[^\x00-\x1f\x7f]{1,200}")  # printable, one line


class TakedownError(Exception):
    pass


@dataclass
class Result:
    source: str
    source_id: str
    status: str  # removed, blocked, already_removed, would_remove, would_block
    removed: dict[str, int] = field(default_factory=dict)


def run(
    db: Database,
    source_id: str,
    reference: str,
    requested_at: datetime,
    now: datetime,
    block_only: bool = False,
    dry_run: bool = False,
) -> Result:
    if not SOURCE_ID.fullmatch(source_id):
        raise TakedownError(f"{source_id!r} isn't a Ticketmaster event ID")
    if not REFERENCE.fullmatch(reference):
        raise TakedownError("the reference must be 1 to 200 printable characters")
    if requested_at.tzinfo is None or requested_at > now:
        raise TakedownError("requested_at must have an offset and not be in the future")
    if dry_run and not db.read_only:
        raise ValueError("a dry run needs a read-only database connection")

    if db.takedown_recorded(SOURCE, source_id) is not None:
        db.conn.rollback()
        return Result(SOURCE, source_id, "already_removed")
    exists = db.event_exists(SOURCE, source_id)
    db.conn.rollback()
    if not exists and not block_only:
        raise TakedownError(
            f"no {SOURCE} event {source_id!r} is stored. Check the ID, or pass --block-only "
            "to block it from future ingest anyway (for example, once it has ended)."
        )
    if dry_run:
        return Result(SOURCE, source_id, "would_remove" if exists else "would_block")

    run_id = db.start_run(JOB, None, now)
    with db.conn.transaction():
        removed = db.take_down(SOURCE, source_id, reference, requested_at)
    counts = RunCounts(deleted=removed["events"] + removed["snapshots"] + removed["venues"])
    db.finish_run(run_id, datetime.now(UTC) - now, counts)
    return Result(SOURCE, source_id, "removed" if removed["events"] else "blocked", removed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs.takedown")
    parser.add_argument("--source-id", required=True, help="the Ticketmaster event ID")
    parser.add_argument("--reference", required=True, help="the request's ticket or subject")
    parser.add_argument("--requested-at", help="when the request arrived (ISO 8601, offset)")
    parser.add_argument("--block-only", action="store_true", help="block even if not stored")
    parser.add_argument("--dry-run", action="store_true", help="check, change nothing")
    args = parser.parse_args(argv)

    now = datetime.now(UTC).replace(microsecond=0)
    requested_at = datetime.fromisoformat(args.requested_at) if args.requested_at else now
    db = Database.connect(config.from_env().db_url, read_only=args.dry_run)
    try:
        result = run(
            db,
            args.source_id.strip(),
            args.reference.strip(),
            requested_at,
            now,
            block_only=args.block_only,
            dry_run=args.dry_run,
        )
    except TakedownError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(json.dumps(asdict(result)))
    hours = (now - requested_at).total_seconds() / 3600
    if not args.dry_run and hours > 24:
        print(f"::warning::removed {hours:.1f} hours after the request, past the 24 hours")
    return 0


if __name__ == "__main__":
    sys.exit(main())
