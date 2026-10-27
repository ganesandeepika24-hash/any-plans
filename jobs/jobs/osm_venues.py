"""OpenStreetMap venues from Geofabrik's Greater London extract (decision 0009).

Usage: python -m jobs.osm_venues [--dry-run] [--file PATH]

The documented selection, as an Overpass query. The job reads the Geofabrik extract
instead of calling Overpass, and applies the same rules itself:

    [out:json][timeout:300];
    area(id:3600175342)->.london;
    (
      nwr["amenity"~"^(restaurant|bar|pub|theatre)$"]["name"](area.london);
      nwr["tourism"~"^(museum|gallery)$"]["name"](area.london);
    );
    out center tags;

Relations (multipolygons) are skipped for now. Ways use the centre of their bounding box,
as Overpass's `out center` does.

ODbL (decision 0006): show "© OpenStreetMap contributors" with a link to
openstreetmap.org/copyright wherever these venues appear, and never match or merge them
with Ticketmaster or Skiddle venues.
"""

import argparse
import json
import re
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import osmium
from shapely import Point
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union
from shapely.prepared import prep

from jobs import config
from jobs.db import Database, RunCounts

JOB = "osm_venues"
CITY_ID = "london"
SOURCE = "osm"
EXTRACT_URL = (
    "https://download.geofabrik.de/europe/united-kingdom/england/greater-london-latest.osm.pbf"
)
STATE_URL = (
    "https://download.geofabrik.de/europe/united-kingdom/england/greater-london-updates/state.txt"
)
USER_AGENT = "any-plans/0.1 (github.com/ganesandeepika24-hash/any-plans)"
BOUNDARY_RELATION = 175342  # Greater London
MAX_AGE = timedelta(days=2)  # older extracts update venues but delete none
DELETE_FLOOR = 0.9  # delete only if this run saw at least 90% of the stored OSM venues

AMENITY_KINDS = {"restaurant": "restaurant", "bar": "bar", "pub": "pub", "theatre": "theatre"}
TOURISM_KINDS = {"museum": "museum", "gallery": "gallery"}
BOOKING_KEYS = ("website:booking", "reservation:website", "reservation:url")
WEBSITE_KEYS = ("website", "contact:website")


class ExtractError(Exception):
    """The extract can't be used: it didn't parse, or the boundary couldn't be built."""


@dataclass(frozen=True)
class OsmVenue:
    source_id: str
    name: str
    kind: str
    address: str | None
    lat: float
    lon: float
    booking_url: str | None
    website_url: str | None

    def row(self) -> tuple:
        """The stored fields, in the order Database.osm_venues returns them."""
        return (
            self.name,
            self.kind,
            self.address,
            self.booking_url,
            self.website_url,
            round(self.lat, 7),
            round(self.lon, 7),
        )


# Tags -----------------------------------------------------------------------------


def kind_of(tags: dict[str, str]) -> str | None:
    return AMENITY_KINDS.get(tags.get("amenity", "")) or TOURISM_KINDS.get(tags.get("tourism", ""))


def first(tags: dict[str, str], keys: tuple[str, ...]) -> str | None:
    return next((tags[k].strip() for k in keys if tags.get(k, "").strip()), None)


def address(tags: dict[str, str]) -> str | None:
    street = " ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p)
    text = ", ".join(
        p for p in (tags.get("addr:housename"), street, tags.get("addr:postcode")) if p
    )
    return text or None


def venue(source_id: str, tags: dict[str, str], lat: float, lon: float) -> OsmVenue | None:
    """A venue from a feature's tags, or None if it has no name."""
    name = tags.get("name", "").strip()
    kind = kind_of(tags)
    if not name or kind is None:
        return None
    return OsmVenue(
        source_id=source_id,
        name=name,
        kind=kind,
        address=address(tags),
        lat=lat,
        lon=lon,
        booking_url=first(tags, BOOKING_KEYS),
        website_url=first(tags, WEBSITE_KEYS),
    )


# The extract ----------------------------------------------------------------------


@dataclass
class Extract:
    venues: list[OsmVenue]
    fetched: int  # features with a matching tag, before any filter
    skipped: Counter


def read_extract(path: Path, boundary_relation: int = BOUNDARY_RELATION) -> Extract:
    """Read venues inside the boundary relation. Raises ExtractError if the file doesn't
    parse in full or the boundary can't be built from it."""
    fetched, skipped = 0, Counter()
    outer, inner = set(), set()
    boundary_found = False
    candidates: list[tuple[str, dict[str, str], float, float]] = []
    boundary_lines: dict[int, LineString] = {}
    try:
        for rel in osmium.FileProcessor(str(path), osmium.osm.RELATION):
            if rel.id == boundary_relation:
                boundary_found = True
                for m in rel.members:
                    if m.type == "w" and m.role in ("outer", ""):
                        outer.add(m.ref)
                    elif m.type == "w" and m.role == "inner":
                        inner.add(m.ref)
            elif kind_of(dict(rel.tags)) is not None:
                fetched += 1
                skipped["multipolygon"] += 1

        entities = osmium.osm.NODE | osmium.osm.WAY
        for obj in osmium.FileProcessor(str(path), entities).with_locations():
            if obj.is_way() and (obj.id in outer or obj.id in inner):
                points = [(n.lon, n.lat) for n in obj.nodes if n.location.valid()]
                if len(points) == len(obj.nodes) and len(points) >= 2:
                    boundary_lines[obj.id] = LineString(points)
            tags = dict(obj.tags)
            if kind_of(tags) is None:
                continue
            fetched += 1
            if obj.is_node():
                candidates.append((f"node/{obj.id}", tags, obj.location.lat, obj.location.lon))
            else:
                points = [(n.lon, n.lat) for n in obj.nodes if n.location.valid()]
                if not points:
                    skipped["no location"] += 1
                    continue
                lons, lats = [p[0] for p in points], [p[1] for p in points]
                # The bounding box's centre, at OSM's 7 decimal places.
                centre = (
                    round((min(lats) + max(lats)) / 2, 7),
                    round((min(lons) + max(lons)) / 2, 7),
                )
                candidates.append((f"way/{obj.id}", tags, *centre))
    except RuntimeError as error:
        raise ExtractError(f"the extract didn't parse: {error}") from None

    area = _boundary(boundary_relation, boundary_found, outer, inner, boundary_lines)
    inside = prep(area)
    venues = []
    for source_id, tags, lat, lon in candidates:
        found = venue(source_id, tags, lat, lon)
        if found is None:
            skipped["unnamed"] += 1
        elif not inside.covers(Point(lon, lat)):
            skipped["outside boundary"] += 1
        else:
            venues.append(found)
    return Extract(venues=venues, fetched=fetched, skipped=skipped)


def _boundary(relation, found, outer, inner, lines):
    if not found:
        raise ExtractError(f"boundary relation {relation} is not in the extract")
    missing = (outer | inner) - set(lines)
    if not outer or missing:
        raise ExtractError(
            f"boundary relation {relation} is incomplete: {len(missing)} of "
            f"{len(outer | inner)} member ways missing or without node locations"
        )
    area = unary_union(list(polygonize([lines[w] for w in outer])))
    holes = [lines[w] for w in inner]
    if holes:
        area = area.difference(unary_union(list(polygonize(holes))))
    if area.is_empty:
        raise ExtractError(f"boundary relation {relation} doesn't form a closed area")
    return area


def replication_timestamp(path: Path) -> datetime | None:
    """The extract's replication timestamp from its header, if it has one."""
    value = osmium.FileProcessor(str(path)).header.get("osmosis_replication_timestamp")
    return _timestamp(value) if value else None


def state_timestamp(client: httpx.Client) -> datetime | None:
    """Fallback: the timestamp line of Geofabrik's state.txt for the extract."""
    response = client.get(STATE_URL)
    response.raise_for_status()
    match = re.search(r"^timestamp=(.+)$", response.text, re.MULTILINE)
    return _timestamp(match.group(1)) if match else None


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("\\", "").replace("Z", "+00:00")).astimezone(UTC)


def download(client: httpx.Client, directory: Path) -> Path:
    """Stream the extract to `directory`, named as Geofabrik's redirect names it."""
    with client.stream("GET", EXTRACT_URL, follow_redirects=True) as response:
        response.raise_for_status()
        target = directory / (Path(response.url.path).name or "extract.osm.pbf")
        with target.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)
    return target


# Run ------------------------------------------------------------------------------


@dataclass
class Summary:
    dry_run: bool
    counts: RunCounts
    extract: str | None = None
    data_as_of: str | None = None
    complete: bool = False
    incomplete_because: list[str] = field(default_factory=list)
    unchanged: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    vanished: int = 0
    kept_with_curated_pages: int = 0
    floor: dict[str, Any] = field(default_factory=dict)
    deletions: str = ""


def run(
    db: Database,
    path: Path,
    now: datetime,
    dry_run: bool,
    data_as_of: datetime | None,
    extract_name: str | None = None,
    boundary_relation: int = BOUNDARY_RELATION,
    delete_floor: float = DELETE_FLOOR,
) -> Summary:
    if dry_run and not db.read_only:
        raise ValueError("a dry run needs a read-only database connection")
    started = time.monotonic()  # duration from a real clock: `now` may be fixed in tests
    summary = Summary(dry_run=dry_run, counts=RunCounts(), extract=extract_name)
    counts = summary.counts
    run_id = None if dry_run else db.start_run(JOB, CITY_ID, now)
    finished = False
    try:
        if not dry_run:
            db.set_data_as_of(run_id, data_as_of)
        summary.data_as_of = data_as_of.isoformat() if data_as_of else None

        extract = read_extract(path, boundary_relation)  # raises before any venue is written
        counts.fetched = extract.fetched
        summary.skipped = dict(extract.skipped)

        # Complete: parsed in full (or read_extract raised), boundary built, and fresh.
        if data_as_of is None:
            summary.incomplete_because.append("no replication timestamp")
        elif now - data_as_of > MAX_AGE:
            summary.incomplete_because.append(
                f"extract is {now - data_as_of} old (limit {MAX_AGE})"
            )
        summary.complete = not summary.incomplete_because

        stored = db.osm_venues()
        with_pages = db.osm_venues_with_curated_pages()
        seen = {v.source_id for v in extract.venues}
        vanished = set(stored) - seen
        summary.vanished = len(vanished)
        summary.kept_with_curated_pages = len(vanished & with_pages)
        seen_stored = len(set(stored) & seen)
        ratio = seen_stored / len(stored) if stored else 1.0
        summary.floor = {
            "stored": len(stored),
            "seen_of_stored": seen_stored,
            "ratio": round(ratio, 4),
            "holds": ratio >= delete_floor,
        }
        delete = summary.complete and summary.floor["holds"]
        summary.deletions = (
            "allowed"
            if delete
            else "skipped: run incomplete"
            if not summary.complete
            else f"skipped: saw {ratio:.1%} of stored OSM venues, below {delete_floor:.0%}"
        )

        if dry_run:
            for v in extract.venues:
                if v.source_id not in stored:
                    counts.new += 1
                elif stored[v.source_id] != v.row():
                    counts.updated += 1
            if delete:
                counts.deleted = len(vanished - with_pages)
            db.conn.rollback()
        else:
            with db.conn.transaction():
                counts.new, counts.updated = db.upsert_osm_venues(CITY_ID, extract.venues)
                if delete:
                    counts.deleted, _ = db.delete_vanished_osm_venues(sorted(seen))
        summary.unchanged = len(extract.venues) - counts.new - counts.updated
        finished = True
        return summary
    except BaseException:
        db.conn.rollback()
        raise
    finally:
        if not dry_run and finished:  # a crashed run keeps duration_ms null (0006)
            db.finish_run(run_id, timedelta(seconds=time.monotonic() - started), counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs.osm_venues")
    parser.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    parser.add_argument("--file", type=Path, help="read this extract instead of downloading")
    args = parser.parse_args(argv)

    db = Database.connect(config.from_env().db_url, read_only=args.dry_run)
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        with (
            httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}) as client,
            tempfile.TemporaryDirectory() as tmp,
        ):
            path = args.file or download(client, Path(tmp))
            data_as_of = replication_timestamp(path)
            if data_as_of is None and args.file is None:
                data_as_of = state_timestamp(client)
            summary = run(db, path, now, args.dry_run, data_as_of, extract_name=path.name)
    finally:
        db.close()
    print(json.dumps(asdict(summary), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
