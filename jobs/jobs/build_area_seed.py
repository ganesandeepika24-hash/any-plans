"""Build db/seed/areas.sql: London's boroughs, neighbourhoods and landmarks (decision 0021).

Usage: python -m jobs.build_area_seed [--file PATH] [--out PATH]

Run by hand when the areas need refreshing, then review the diff and commit it. It
writes no database rows: loading the seed does that (CI, and once per refresh against
Supabase). Three inputs:

- ONS's December 2024 local authority districts: the 33 London boroughs (codes E09...),
  with their names and points. Source: Office for National Statistics licensed under the
  Open Government Licence v.3.0. Contains OS data © Crown copyright and database right
  2024.
- db/seed/area_curation.json: borough aliases, the hand-picked neighbourhoods and
  aliases, and the landmarks, each naming its OSM object.
- Geofabrik's Greater London extract (as jobs.osm_venues reads it): named places tagged
  town, suburb, neighbourhood or quarter inside Greater London, and the landmarks'
  objects. © OpenStreetMap contributors, ODbL.

When names clash, the first of these keeps it: boroughs, the hand-picked areas and
landmarks, towns, suburbs, then neighbourhoods and quarters. Two OSM places of the same
rank with the same name are both left out, and the run lists them.
"""

import argparse
import json
import re
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from shapely import Point

from jobs.osm_venues import BOUNDARY_RELATION, USER_AGENT, download, replication_timestamp, scan

REPO = Path(__file__).resolve().parents[2]
CURATION = REPO / "db" / "seed" / "area_curation.json"
SEED = REPO / "db" / "seed" / "areas.sql"
CITY_ID = "london"
CITY_NAME = "London"
ONS_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Local_Authority_Districts_December_2024_Boundaries_UK_BUC/FeatureServer/0/query"
)
ONS_PARAMS = {
    "where": "LAD24CD LIKE 'E09%'",
    "outFields": "LAD24CD,LAD24NM,LONG,LAT",
    "returnGeometry": "false",
    "f": "json",
}
BOROUGHS = 33
GSS = re.compile(r"^E09\d{6}$")
# OSM place values kept, by rank: a higher rank keeps a name another place also has.
PLACE_RANK = {"town": 3, "suburb": 2, "neighbourhood": 1, "quarter": 1}
BOROUGH_RADIUS_KM = 6  # decision 0006
NEIGHBOURHOOD_RADIUS_KM = 1
LANDMARK_RADIUS_KM = 1  # step 5 decisions


class SeedError(Exception):
    """An input is missing or doesn't look as expected; nothing is written."""


@dataclass(frozen=True)
class Borough:
    code: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class AreaRow:
    id: str
    name: str
    kind: str  # borough, neighbourhood or landmark
    aliases: tuple[str, ...]
    lat: float
    lon: float
    radius_km: float
    gss_code: str | None
    source: str  # the comment: where the point came from


@dataclass
class Built:
    rows: list[AreaRow]
    taken: list[str] = field(default_factory=list)  # OSM names an earlier area has
    duplicates: dict[str, list[str]] = field(default_factory=dict)  # name -> OSM ids


def normalise(name: str) -> str:
    """As the agent matches names (agent/app/understand/resolve.py; a test compares)."""
    text = name.casefold().replace("&", " and ")
    text = re.sub(r"[’'.]", "", text)
    text = " ".join(re.sub(r"[^\w]+", " ", text).split())
    return text.removeprefix("the ")


def slug(name: str) -> str:
    return "-".join(normalise(name).split())


# Inputs ---------------------------------------------------------------------------


def fetch_boroughs(client: httpx.Client) -> list[Borough]:
    response = client.get(ONS_URL, params=ONS_PARAMS)
    response.raise_for_status()
    return parse_boroughs(response.json())


def parse_boroughs(body: dict) -> list[Borough]:
    try:
        boroughs = [
            Borough(
                code=f["attributes"]["LAD24CD"],
                name=f["attributes"]["LAD24NM"].strip(),
                lat=float(f["attributes"]["LAT"]),
                lon=float(f["attributes"]["LONG"]),
            )
            for f in body["features"]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise SeedError(f"ONS reply isn't the expected shape: {exc!r}") from None
    codes = {b.code for b in boroughs}
    if len(boroughs) != BOROUGHS or len(codes) != BOROUGHS or not all(map(GSS.match, codes)):
        raise SeedError(f"expected {BOROUGHS} distinct E09 codes from ONS, got {sorted(codes)}")
    return sorted(boroughs, key=lambda b: b.code)


def read_osm(
    path: Path,
    landmarks_wanted: dict[str, str],
    boundary_relation: int = BOUNDARY_RELATION,
):
    """The named places inside the boundary, and the landmarks' objects, by OSM id.
    `landmarks_wanted` maps each landmark's OSM id to its name in OSM."""
    landmark_ids, landmark_names = set(landmarks_wanted), set(landmarks_wanted.values())

    def wanted(tags: dict[str, str]) -> bool:
        name = tags.get("name", "").strip()
        return bool(name) and (tags.get("place") in PLACE_RANK or name in landmark_names)

    found = scan(path, wanted, boundary_relation)
    places, landmarks = [], {}
    for source_id, tags, lat, lon in found.candidates:
        if source_id in landmark_ids:
            landmarks[source_id] = (lat, lon)
        if tags.get("place") in PLACE_RANK and found.boundary.covers(Point(lon, lat)):
            places.append((source_id, tags["name"].strip(), tags["place"], lat, lon))
    missing = landmark_ids - set(landmarks)
    if missing:
        raise SeedError(f"landmark objects not in the extract: {sorted(missing)}")
    return places, landmarks


# Building -------------------------------------------------------------------------


def build(
    boroughs: list[Borough],
    curation: dict,
    places: list[tuple[str, str, str, float, float]],
    landmark_points: dict[str, tuple[float, float]],
) -> Built:
    rows: list[AreaRow] = []
    for b in boroughs:
        area_id = f"{CITY_ID}-{slug(b.name)}"
        if area_id not in curation["boroughs"]:
            raise SeedError(f"ONS borough {b.name!r} has no entry {area_id!r} in the curation")
        aliases = tuple(curation["boroughs"][area_id]["aliases"])
        rows.append(
            AreaRow(
                area_id,
                b.name,
                "borough",
                aliases,
                b.lat,
                b.lon,
                BOROUGH_RADIUS_KM,
                b.code,
                f"ONS {b.code}",
            )
        )
    if len(rows) != len(curation["boroughs"]):
        raise SeedError("the curation lists a borough ONS doesn't have")
    for n in curation["neighbourhoods"]:
        rows.append(
            AreaRow(
                n["id"],
                n["name"],
                "neighbourhood",
                tuple(n["aliases"]),
                n["lat"],
                n["lon"],
                NEIGHBOURHOOD_RADIUS_KM,
                None,
                f"{n['osm']} (hand-picked)",
            )
        )
    for lm in curation["landmarks"]:
        lat, lon = landmark_points[lm["osm"]]
        rows.append(
            AreaRow(
                lm["id"],
                lm["name"],
                "landmark",
                tuple(lm["aliases"]),
                lat,
                lon,
                LANDMARK_RADIUS_KM,
                None,
                f"{lm['osm']} (landmark)",
            )
        )

    # The city itself and "central London" are the city's row, never an area (M-02).
    taken = {normalise(CITY_NAME), normalise(f"central {CITY_NAME}")}
    for r in rows:
        taken |= {normalise(n) for n in (r.name, *r.aliases)}
    ids = {r.id for r in rows}
    built = Built(rows)

    by_name: dict[str, list] = defaultdict(list)
    for place in places:
        by_name[normalise(place[1])].append(place)
    for key in sorted(by_name):
        group = by_name[key]
        if key in taken:
            built.taken.append(group[0][1])
            continue
        top = max(PLACE_RANK[p[2]] for p in group)
        best = [p for p in group if PLACE_RANK[p[2]] == top]
        if len(best) > 1:
            built.duplicates[best[0][1]] = sorted(p[0] for p in best)
            continue
        source_id, name, place, lat, lon = best[0]
        area_id = f"{CITY_ID}-{slug(name)}"
        if area_id in ids:
            raise SeedError(f"{name!r} would reuse the id {area_id!r}")
        ids.add(area_id)
        rows.append(
            AreaRow(
                area_id,
                name,
                "neighbourhood",
                (),
                lat,
                lon,
                NEIGHBOURHOOD_RADIUS_KM,
                None,
                f"{source_id} ({place})",
            )
        )
    return built


# Output ---------------------------------------------------------------------------


def _q(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _aliases(aliases: tuple[str, ...]) -> str:
    if not aliases:
        return "'{}'::text[]"
    return "array[" + ", ".join(_q(a) for a in aliases) + "]::text[]"


def render(built: Built, extract_note: str) -> str:
    lines = [
        "-- London's 33 boroughs, its neighbourhoods and a few landmarks (decision 0021).",
        "-- Generated by `python -m jobs.build_area_seed`: don't edit by hand. Change",
        "-- db/seed/area_curation.json and rerun. Safe to run repeatedly. Needs cities.sql first.",
        "--",
        "-- Boroughs: ONS Local Authority Districts (December 2024). Source: Office for National",
        "-- Statistics licensed under the Open Government Licence v.3.0. Contains OS data",
        "-- © Crown copyright and database right 2024.",
        f"-- Neighbourhoods and landmarks: OpenStreetMap, {extract_note}.",
        "-- © OpenStreetMap contributors, ODbL. Each row names its OSM object. Ways use their",
        "-- bounding box's centre. Hand-picked rows keep the points chosen when first seeded.",
        "insert into public.areas"
        " (id, name, kind, aliases, centre_geom, default_radius_km, gss_code, city_id)",
        "select v.id, v.name, v.kind, v.aliases, v.centre_geom::extensions.geography,"
        f" v.default_radius_km, v.gss_code, '{CITY_ID}'",
        "from (values",
    ]
    body = []
    for i, r in enumerate(built.rows):
        sep = "," if i < len(built.rows) - 1 else " "
        gss = _q(r.gss_code) if r.gss_code else "null"
        body.append(
            f"  ({_q(r.id)}, {_q(r.name)}, {_q(r.kind)}, {_aliases(r.aliases)}, "
            f"'SRID=4326;POINT({r.lon:.6f} {r.lat:.6f})', {r.radius_km:g}, {gss}){sep}"
            f"  -- {r.source}"
        )
    tail = [
        ") as v (id, name, kind, aliases, centre_geom, default_radius_km, gss_code)",
        "on conflict (id) do update set",
        "  city_id           = excluded.city_id,",
        "  name              = excluded.name,",
        "  kind              = excluded.kind,",
        "  aliases           = excluded.aliases,",
        "  centre_geom       = excluded.centre_geom,",
        "  default_radius_km = excluded.default_radius_km,",
        "  gss_code          = excluded.gss_code;",
    ]
    return "\n".join(lines + body + tail) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs.build_area_seed")
    parser.add_argument("--file", type=Path, help="read this extract instead of downloading")
    parser.add_argument("--out", type=Path, default=SEED)
    args = parser.parse_args(argv)
    curation = json.loads(CURATION.read_text())
    landmarks_wanted = {lm["osm"]: lm["name"] for lm in curation["landmarks"]}
    with (
        httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}) as client,
        tempfile.TemporaryDirectory() as tmp,
    ):
        try:
            boroughs = fetch_boroughs(client)
            path = args.file or download(client, Path(tmp))
            places, landmarks = read_osm(path, landmarks_wanted)
            built = build(boroughs, curation, places, landmarks)
        except SeedError as exc:
            print(f"build_area_seed: {exc}", file=sys.stderr)
            return 1
        as_of = replication_timestamp(path)
        note = f"Geofabrik's Greater London extract as of {as_of:%d %b %Y}" if as_of else path.name
        args.out.write_text(render(built, note))
    kinds = defaultdict(int)
    for r in built.rows:
        kinds[r.kind] += 1
    print(
        json.dumps(
            {
                "written": str(args.out),
                "rows": len(built.rows),
                "by_kind": dict(kinds),
                "osm_places_seen": len(places),
                "names_taken_by_earlier_areas": built.taken,
                "duplicates_left_out": built.duplicates,
            },
            indent=1,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
