"""Embed new and changed event text with the agent's model (step 1.2d, decision 0009).

Usage: python -m jobs.embed_events [--dry-run]

Each event's text is its title, category, genre, venue name and description. The row
stores the sha256 of the model name and that text in embedding_hash, so a run embeds
only events whose text, or the model, changed since they were last embedded. Only
events users can see are embedded: active, and not a duplicate. Writes go in batches,
each one statement committed on its own, so a failed run keeps the batches before it.
Each batch logs its progress to stderr, so a slow run shows how far it got.

The ingest job runs this after dedupe. Run it alone to backfill. A dry run is read-only,
counts what it would embed and prints a few texts; it never loads the model.
"""

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from jobs import config
from jobs.db import Database, RunCounts
from jobs.embeddings import DIMENSIONS, MODEL_NAME, embed

JOB = "embed_events"
BATCH = 256
SEPARATOR = " · "
SAMPLES = 5


def embedding_text(
    title: str, category: str, genre: str | None, venue: str | None, description: str | None
) -> str:
    # Description last: the model truncates long input, so that's the part to lose.
    parts = (title, category, genre, venue, description)
    return SEPARATOR.join(p.strip() for p in parts if p and p.strip())


def text_hash(text: str, model: str = MODEL_NAME) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


@dataclass
class Summary:
    dry_run: bool
    counts: RunCounts
    unchanged: int = 0
    samples: list[str] = field(default_factory=list)


def run(
    db: Database,
    now: datetime,
    dry_run: bool,
    embedder: Callable[[list[str]], list[list[float]]] = embed,
    batch_size: int = BATCH,
) -> Summary:
    """fetched: events read. new: embedded for the first time (or first since migration
    009). updated: re-embedded because the text or model changed."""
    if dry_run and not db.read_only:
        raise ValueError("a dry run needs a read-only database connection")
    started = time.monotonic()  # duration from a real clock: `now` may be fixed in tests
    summary = Summary(dry_run=dry_run, counts=RunCounts())
    counts = summary.counts
    run_id = None if dry_run else db.start_run(JOB, None, now)  # spans cities
    finished = False
    try:
        rows = db.embedding_candidates()
        counts.fetched = len(rows)
        pending = []
        for row in rows:
            text = embedding_text(
                row["title"], row["category"], row["genre"], row["venue"], row["description"]
            )
            digest = text_hash(text)
            if digest != row["embedding_hash"]:
                pending.append((row["id"], text, digest))
                if row["embedding_hash"] is None:
                    counts.new += 1
                else:
                    counts.updated += 1
        summary.unchanged = counts.fetched - len(pending)
        summary.samples = [text for _, text, _ in pending[:SAMPLES]]

        if not dry_run:
            for start in range(0, len(pending), batch_size):
                batch = pending[start : start + batch_size]
                vectors = embedder([text for _, text, _ in batch])
                for vector in vectors:
                    if len(vector) != DIMENSIONS:
                        raise ValueError(f"expected {DIMENSIONS} dimensions, got {len(vector)}")
                rows = [
                    (event_id, vector, digest)
                    for (event_id, _, digest), vector in zip(batch, vectors, strict=True)
                ]
                with db.conn.transaction():
                    db.set_embeddings(rows)
                print(
                    f"embed_events: {start + len(batch)} of {len(pending)} written",
                    file=sys.stderr,
                    flush=True,
                )
        finished = True
        return summary
    except BaseException:
        db.conn.rollback()
        raise
    finally:
        if dry_run:
            db.conn.rollback()
        elif finished:  # a crashed run keeps duration_ms null (0006)
            db.finish_run(run_id, timedelta(seconds=time.monotonic() - started), counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs.embed_events")
    parser.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    args = parser.parse_args(argv)

    db = Database.connect(config.from_env().db_url, read_only=args.dry_run)
    try:
        summary = run(db, datetime.now(UTC).replace(microsecond=0), args.dry_run)
    finally:
        db.close()
    print(json.dumps({"embed_events": asdict(summary)}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
