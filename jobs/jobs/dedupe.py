"""Cross-source duplicates: link the less complete record of a pair to the richer one.

Usage: python -m jobs.dedupe [--dry-run]

Two events are the same when they come from different sources, in the same city, on
the same local date, start within 30 minutes, are within 100 m (measured on the
geography columns), have the same status, and have matching titles. Each event is
linked at most once. The duplicate keeps its row and source link; duplicate_of hides
it from the public (migration 007, decision 0009). Every run rebuilds a city's links
from scratch, so reruns give the same result. A dry run is read-only and prints the
pairs it would link.
"""

import argparse
import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from jobs import config
from jobs.db import Database, RunCounts

JOB = "dedupe"
MAX_DISTANCE_M = 100.0
MAX_START_GAP = timedelta(minutes=30)
TITLE_THRESHOLD = 0.85  # to be calibrated after a real Ticketmaster dry run
FILLER = frozenset({"the", "a", "an", "live", "presents", "tickets", "at", "in", "london"})
RICH_FIELDS = (
    "end_at",
    "price_min",
    "price_max",
    "currency",
    "image_url",
    "url",
    "genre",
    "venue_id",
)
_WORD = re.compile(r"[a-z0-9]+")


# Titles -------------------------------------------------------------------------


def tokens(title: str) -> set[str]:
    text = unicodedata.normalize("NFKD", title.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"['’]", "", text).replace("&", " and ")
    return {w for w in _WORD.findall(text) if w not in FILLER}


def title_score(x: str, y: str) -> float:
    tx, ty = tokens(x), tokens(y)
    if not tx or not ty:
        return 0.0
    shared = tx & ty
    single_identical = len(tx) == 1 and tx == ty
    if len(shared) < 2 and not single_identical:  # "Jazz" must not match "Jazz Night"
        return 0.0
    overlap = len(shared) / min(len(tx), len(ty))
    ordered = SequenceMatcher(None, " ".join(sorted(tx)), " ".join(sorted(ty))).ratio()
    return max(overlap, ordered)


def titles_match(x: str, y: str) -> bool:
    return title_score(x, y) >= TITLE_THRESHOLD


# Pairs ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    id: UUID
    source: str
    source_id: str
    title: str
    start_at: datetime
    status: str
    city_id: str
    filled: int  # how many of RICH_FIELDS are set

    def richness(self) -> tuple:
        # More filled fields wins; ties go to Ticketmaster, then source name, then source_id.
        return (self.filled, self.source == "ticketmaster", self.source, self.source_id)

    def label(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass(frozen=True)
class Pair:
    a: Record
    b: Record
    metres: float
    score: float

    @property
    def gap(self) -> timedelta:
        return abs(self.a.start_at - self.b.start_at)


def record(row: dict[str, Any], side: str) -> Record:
    get = lambda name: row[f"{side}_{name}"]  # noqa: E731
    return Record(
        id=get("id"),
        source=get("source"),
        source_id=get("source_id"),
        title=get("title"),
        start_at=get("start_at"),
        status=get("status"),
        city_id=get("city_id"),
        filled=sum(get(name) is not None for name in RICH_FIELDS),
    )


def is_duplicate(a: Record, b: Record, metres: float | None, tz: ZoneInfo) -> bool:
    return (
        a.source != b.source
        and a.city_id == b.city_id
        and a.start_at.astimezone(tz).date() == b.start_at.astimezone(tz).date()
        and abs(a.start_at - b.start_at) <= MAX_START_GAP
        and metres is not None
        and metres <= MAX_DISTANCE_M
        and a.status == b.status
        and titles_match(a.title, b.title)
    )


def choose_links(pairs: list[Pair]) -> list[tuple[Record, Record, Pair]]:
    """One-to-one: best title score first, then smallest time gap, then distance.
    Returns (kept, duplicate, pair) for each link."""
    ordered = sorted(pairs, key=lambda p: (-p.score, p.gap, p.metres, p.a.label(), p.b.label()))
    used: set[UUID] = set()
    links = []
    for pair in ordered:
        if pair.a.id in used or pair.b.id in used:
            continue
        kept = max(pair.a, pair.b, key=Record.richness)
        duplicate = pair.b if kept is pair.a else pair.a
        links.append((kept, duplicate, pair))
        used |= {pair.a.id, pair.b.id}
    return links


# Run ------------------------------------------------------------------------------


@dataclass
class CityResult:
    city: str
    counts: RunCounts
    links: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Summary:
    dry_run: bool
    cities: list[CityResult] = field(default_factory=list)


def run(db: Database, now: datetime, dry_run: bool) -> Summary:
    if dry_run and not db.read_only:
        raise ValueError("a dry run needs a read-only database connection")
    summary = Summary(dry_run=dry_run)
    for city_id, tz in db.dedupe_cities():
        summary.cities.append(_run_city(db, city_id, tz, now, dry_run))
    if dry_run:
        db.conn.rollback()
    return summary


def _run_city(db: Database, city_id: str, tz: ZoneInfo, now: datetime, dry_run: bool) -> CityResult:
    started = time.monotonic()
    result = CityResult(city=city_id, counts=RunCounts())
    counts = result.counts
    run_id = None if dry_run else db.start_run(JOB, city_id, now)
    finished = False
    try:
        rows = db.dedupe_candidates(city_id, MAX_START_GAP, MAX_DISTANCE_M)
        counts.fetched = len(rows)
        pairs = []
        for row in rows:
            a, b = record(row, "a"), record(row, "b")
            if is_duplicate(a, b, row["metres"], tz):
                pairs.append(Pair(a, b, row["metres"], title_score(a.title, b.title)))
        links = choose_links(pairs)

        before = db.current_links(city_id)
        after = {duplicate.id: kept.id for kept, duplicate, _ in links}
        counts.new = sum(1 for d in after if d not in before)
        counts.updated = sum(1 for d, k in before.items() if after.get(d) != k)
        result.links = [
            {
                "kept": kept.label(),
                "duplicate": duplicate.label(),
                "kept_title": kept.title,
                "duplicate_title": duplicate.title,
                "score": round(pair.score, 3),
                "gap_minutes": round(pair.gap.total_seconds() / 60, 1),
                "metres": round(pair.metres, 1),
            }
            for kept, duplicate, pair in links
        ]

        if not dry_run:
            with db.conn.transaction():
                db.set_duplicates(city_id, after)
        finished = True
        return result
    except BaseException:
        db.conn.rollback()
        raise
    finally:
        if not dry_run and finished:  # a crashed run keeps duration_ms null (0006)
            db.finish_run(run_id, timedelta(seconds=time.monotonic() - started), counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs.dedupe")
    parser.add_argument("--dry-run", action="store_true", help="print the links, write nothing")
    args = parser.parse_args(argv)

    db = Database.connect(config.from_env().db_url, read_only=args.dry_run)
    try:
        summary = run(db, datetime.now(UTC).replace(microsecond=0), dry_run=args.dry_run)
    finally:
        db.close()
    print(json.dumps(asdict(summary), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
