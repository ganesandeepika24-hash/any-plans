"""OSM venues (decision 0009), with migration 008's job_runs.data_as_of.

Fixture tests/fixtures/osm/city-of-london.osm is real OpenStreetMap data (© OpenStreetMap
contributors, ODbL), fetched from Overpass on 27 Oct 2026 with `out body` (no user names
or IDs). It holds:

- relation 51800, City of London's boundary, with its member ways and nodes. It stands
  in for Greater London (relation 175342) so the fixture stays small.
- node/21686579 "White Haus", a pub (inside the City)
- node/4513341393 "Bank of England Museum" (inside)
- node/7792756673 "Barbican Art Gallery" (inside)
- node/5157934313 "Barbican Theatre" (inside)
- way/815659586 "Wagamama", a restaurant way with only contact:website (inside)
- node/668074193, an unnamed restaurant (inside)
- way/97176335 "Vecchio Parioli", 129 Aldersgate Street: just over the City's edge in
  Islington, so outside the boundary
- node/10266440600 "The Admiral Hardy", Greenwich: a pub with website:booking, outside
- relation/1239793 "Shakespeare's Globe", a multipolygon theatre (skipped)
"""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import osmium
import pytest

from jobs import osm_venues
from jobs.db import Database
from jobs.osm_venues import ExtractError, OsmVenue, read_extract, venue
from tests.conftest import NOW

FIXTURE = Path(__file__).parent / "fixtures" / "osm" / "city-of-london.osm"
CITY = 51800
INSIDE = {"node/21686579", "node/4513341393", "node/7792756673", "node/5157934313", "way/815659586"}
FRESH = NOW - timedelta(hours=30)


def fixture_root():
    return ET.parse(FIXTURE).getroot()  # noqa: S314 (our own committed fixture, not input)


def tags_of(osm_type, osm_id):
    root = fixture_root()
    el = root.find(f"{osm_type}[@id='{osm_id}']")
    return {t.get("k"): t.get("v") for t in el.findall("tag")}


# Selection (no database) ------------------------------------------------------------


def test_the_fixture_selection():
    extract = read_extract(FIXTURE, boundary_relation=CITY)
    assert {v.source_id for v in extract.venues} == INSIDE
    assert extract.fetched == 9  # every feature with a matching tag, before the filters
    assert dict(extract.skipped) == {"multipolygon": 1, "unnamed": 1, "outside boundary": 2}


@pytest.mark.parametrize(
    ("tags", "kind"),
    [
        ({"amenity": "restaurant"}, "restaurant"),
        ({"amenity": "bar"}, "bar"),
        ({"amenity": "pub"}, "pub"),
        ({"amenity": "theatre"}, "theatre"),
        ({"tourism": "museum"}, "museum"),
        ({"tourism": "gallery"}, "gallery"),
        ({"amenity": "pub", "tourism": "museum"}, "pub"),  # amenity wins
        ({"amenity": "cafe"}, None),
        ({"shop": "art"}, None),
    ],
)
def test_kind_mapping(tags, kind):
    assert osm_venues.kind_of(tags) == kind


def test_every_kind_is_allowed_by_venues_kind_check():
    migration = (
        Path(__file__).resolve().parents[2]
        / "db"
        / "migrations"
        / "007_duplicates_and_venue_fields.sql"
    ).read_text()
    check = migration[migration.index("add constraint venues_kind_check") :]
    for kind in {*osm_venues.AMENITY_KINDS.values(), *osm_venues.TOURISM_KINDS.values()}:
        assert f"'{kind}'" in check


def test_real_pub_with_a_booking_tag():
    v = venue("node/10266440600", tags_of("node", "10266440600"), 51.48, -0.009)
    assert (v.name, v.kind) == ("The Admiral Hardy", "pub")
    assert v.booking_url.startswith("https://admiralhardygreenwich.")  # website:booking
    assert v.website_url.startswith("https://admiralhardygreenwich.")  # website
    assert v.address == "7 College Approach, SE10 9HY"


def test_contact_website_is_used_when_there_is_no_website():
    v = venue("way/815659586", tags_of("way", "815659586"), 51.5, -0.1)
    assert v.website_url == "https://www.wagamama.com/restaurants/london/st-pauls"
    assert v.booking_url is None


@pytest.mark.parametrize(
    ("extra", "booking"),
    [
        (
            {
                "website:booking": "https://a",
                "reservation:website": "https://b",
                "reservation:url": "https://c",
            },
            "https://a",
        ),
        ({"reservation:website": "https://b", "reservation:url": "https://c"}, "https://b"),
        ({"reservation:url": "https://c"}, "https://c"),
        ({"reservation": "required"}, None),  # a booking policy, not a URL
    ],
)
def test_booking_precedence(extra, booking):
    assert (
        venue("node/1", {"amenity": "bar", "name": "X", **extra}, 51.5, -0.1).booking_url == booking
    )


def test_website_precedence():
    tags = {"amenity": "bar", "name": "X", "website": "https://w", "contact:website": "https://c"}
    assert venue("node/1", tags, 51.5, -0.1).website_url == "https://w"


def test_unnamed_features_are_skipped():
    assert venue("node/668074193", tags_of("node", "668074193"), 51.5, -0.1) is None


def test_a_way_uses_the_centre_of_its_bounding_box():
    root = fixture_root()
    refs = [nd.get("ref") for nd in root.find("way[@id='815659586']").findall("nd")]
    nodes = {n.get("id"): (float(n.get("lat")), float(n.get("lon"))) for n in root.findall("node")}
    lats, lons = [nodes[r][0] for r in refs], [nodes[r][1] for r in refs]
    expected = (round((min(lats) + max(lats)) / 2, 7), round((min(lons) + max(lons)) / 2, 7))
    [wagamama] = [v for v in read_extract(FIXTURE, CITY).venues if v.source_id == "way/815659586"]
    assert (wagamama.lat, wagamama.lon) == expected


# Extract errors and dates (no database) --------------------------------------------


def to_pbf(source: Path, target: Path, timestamp: str | None = None) -> Path:
    header = osmium.io.Header()
    if timestamp:
        header.set("osmosis_replication_timestamp", timestamp)
    writer = osmium.SimpleWriter(str(target), 4096, header)
    for obj in osmium.FileProcessor(str(source)):
        writer.add(obj)
    writer.close()
    return target


def test_a_truncated_extract_is_a_parse_error(tmp_path):
    pbf = to_pbf(FIXTURE, tmp_path / "full.osm.pbf")
    broken = tmp_path / "broken.osm.pbf"
    broken.write_bytes(pbf.read_bytes()[: pbf.stat().st_size // 2])
    with pytest.raises(ExtractError, match="didn't parse"):
        read_extract(broken, CITY)


def test_a_missing_boundary_is_an_error():
    with pytest.raises(ExtractError, match="not in the extract"):
        read_extract(FIXTURE, boundary_relation=999999999)


def test_an_incomplete_boundary_is_an_error(tmp_path):
    root = fixture_root()
    member = root.find(f"relation[@id='{CITY}']/member[@role='outer']").get("ref")
    root.remove(root.find(f"way[@id='{member}']"))
    trimmed = tmp_path / "trimmed.osm"
    ET.ElementTree(root).write(trimmed, encoding="utf-8", xml_declaration=True)
    with pytest.raises(ExtractError, match="incomplete"):
        read_extract(trimmed, CITY)


def test_the_replication_timestamp_comes_from_the_pbf_header(tmp_path):
    pbf = to_pbf(FIXTURE, tmp_path / "x.osm.pbf", "2026-10-25T21:21:21Z")
    assert osm_venues.replication_timestamp(pbf) == datetime(2026, 10, 25, 21, 21, 21, tzinfo=UTC)
    assert osm_venues.replication_timestamp(to_pbf(FIXTURE, tmp_path / "y.osm.pbf")) is None


def test_state_txt_fallback():
    # The real state.txt for the 25 Oct 2026 extract, escaped colons included.
    state = (
        "# original OSM minutely replication sequence number 7344597\n"
        "timestamp=2026-10-25T21\\:21\\:21Z\nsequenceNumber=4945\n"
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=state)))
    assert osm_venues.state_timestamp(client) == datetime(2026, 10, 25, 21, 21, 21, tzinfo=UTC)


def test_download_follows_the_redirect_and_keeps_the_dated_name(tmp_path):
    def handler(request):
        if request.url.path.endswith("greater-london-latest.osm.pbf"):
            return httpx.Response(
                307,
                headers={
                    "Location": str(request.url).replace(
                        "greater-london-latest", "greater-london-261025"
                    )
                },
            )
        return httpx.Response(200, content=b"PBF bytes")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    path = osm_venues.download(client, tmp_path)
    assert (path.name, path.read_bytes()) == ("greater-london-261025.osm.pbf", b"PBF bytes")


def test_the_dry_run_flag_opens_a_read_only_connection(monkeypatch, tmp_path):
    opened = []

    class Stop(Exception):
        pass

    def connect(url, read_only):
        opened.append(read_only)
        raise Stop

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://example")
    monkeypatch.setattr(osm_venues.Database, "connect", staticmethod(connect))
    for argv in (["--dry-run", "--file", str(FIXTURE)], ["--file", str(FIXTURE)]):
        with pytest.raises(Stop):
            osm_venues.main(argv)
    assert opened == [True, False]


# Against the database -------------------------------------------------------------


def run(db, **kwargs):
    options = {
        "dry_run": False,
        "data_as_of": FRESH,
        "extract_name": FIXTURE.name,
        "boundary_relation": CITY,
        **kwargs,
    }
    return osm_venues.run(db, FIXTURE, NOW, **options)


def add_stored(sql, source_id, name="Gone", with_page=False):
    [(venue_id,)] = sql(
        "insert into venues (city_id, source, source_id, name, kind) values"
        " ('london', 'osm', %s, %s, 'pub') returning id",
        (source_id, name),
    )
    if with_page:
        sql(
            "insert into curated_pages (venue_id, url) values (%s, 'https://example.com')",
            (venue_id,),
        )


def stored(sql):
    return {r[0] for r in sql("select source_id from venues where source = 'osm'")}


def test_a_complete_run_writes_the_venues_and_a_job_run(db, sql):
    summary = run(db)
    assert stored(sql) == INSIDE
    assert sql(
        "select name, kind, address, website_url from venues where source_id = 'way/815659586'"
    ) == [
        (
            "Wagamama",
            "restaurant",
            "1 Ludgate Hill, EC4M 7AA",
            "https://www.wagamama.com/restaurants/london/st-pauls",
        )
    ]
    assert summary.complete and summary.deletions == "allowed"
    assert sql(
        "select job, city_id, fetched, new, updated, deleted, data_as_of,"
        " duration_ms is not null from job_runs"
    ) == [("osm_venues", "london", 9, 5, 0, 0, FRESH, True)]


def test_a_rerun_changes_nothing_and_a_changed_tag_is_one_update(db, sql, tmp_path):
    run(db)
    again = run(db)
    assert (again.counts.new, again.counts.updated, again.unchanged) == (0, 0, 5)

    changed = tmp_path / "changed.osm"
    changed.write_text(FIXTURE.read_text().replace('v="White Haus"', 'v="White Haus Bar"'))
    third = osm_venues.run(db, changed, NOW, False, FRESH, boundary_relation=CITY)
    assert (third.counts.new, third.counts.updated, third.unchanged) == (0, 1, 4)
    assert sql("select name from venues where source_id = 'node/21686579'") == [("White Haus Bar",)]


def test_every_kind_is_accepted_by_the_database(db, sql):
    kinds = ["restaurant", "bar", "pub", "theatre", "museum", "gallery"]
    venues = [
        OsmVenue(f"node/{i}", f"V{i}", k, None, 51.5, -0.1, None, None) for i, k in enumerate(kinds)
    ]
    with db.conn.transaction():
        assert db.upsert_osm_venues("london", venues) == (6, 0)
    assert sorted(r[0] for r in sql("select kind from venues")) == sorted(kinds)


def test_a_complete_run_removes_vanished_venues_but_keeps_curated_ones(db, sql):
    run(db)
    add_stored(sql, "node/1", "Closed pub")
    add_stored(sql, "node/2", "Curated pub", with_page=True)
    summary = run(db, delete_floor=0.5)  # 5 of 7 stored seen: under the default 90%
    assert stored(sql) == INSIDE | {"node/2"}
    assert (summary.counts.deleted, summary.vanished, summary.kept_with_curated_pages) == (1, 2, 1)
    assert sql("select count(*) from curated_pages") == [(1,)]


def test_an_old_extract_updates_but_deletes_nothing(db, sql):
    add_stored(sql, "node/1")
    summary = run(db, data_as_of=NOW - timedelta(days=3), delete_floor=0.0)
    assert not summary.complete and summary.deletions == "skipped: run incomplete"
    assert stored(sql) == INSIDE | {"node/1"}
    assert sql("select data_as_of from job_runs") == [(NOW - timedelta(days=3),)]


def test_no_replication_timestamp_is_incomplete(db, sql):
    add_stored(sql, "node/1")
    summary = run(db, data_as_of=None, delete_floor=0.0)
    assert summary.incomplete_because == ["no replication timestamp"]
    assert "node/1" in stored(sql)


def test_below_the_90_percent_floor_nothing_is_deleted(db, sql):
    for i in range(10):
        add_stored(sql, f"node/{i + 1}")
    summary = run(db)  # sees 0 of the 10 stored venues
    assert summary.complete and not summary.floor["holds"]
    assert summary.deletions.startswith("skipped: saw 0.0% of stored OSM venues")
    assert len(stored(sql)) == 15


@pytest.mark.parametrize("problem", ["truncated", "missing boundary"])
def test_an_unusable_extract_writes_no_venues(db, sql, tmp_path, problem):
    add_stored(sql, "node/1")
    if problem == "truncated":
        pbf = to_pbf(FIXTURE, tmp_path / "full.osm.pbf")
        path = tmp_path / "broken.osm.pbf"
        path.write_bytes(pbf.read_bytes()[: pbf.stat().st_size // 2])
        relation = CITY
    else:
        path, relation = FIXTURE, 999999999
    with pytest.raises(ExtractError):
        osm_venues.run(db, path, NOW, False, FRESH, boundary_relation=relation)
    assert stored(sql) == {"node/1"}
    assert sql("select job, duration_ms, data_as_of from job_runs") == [("osm_venues", None, FRESH)]


def test_a_crash_keeps_duration_null(db, sql, monkeypatch):
    def fail(*args):
        raise RuntimeError("write failed")

    monkeypatch.setattr(db, "upsert_osm_venues", fail)
    with pytest.raises(RuntimeError):
        run(db)
    assert sql("select job, duration_ms from job_runs") == [("osm_venues", None)]
    assert stored(sql) == set()


def test_dry_run_counts_and_writes_nothing(db_url, db, sql):
    run(db)
    add_stored(sql, "node/1")
    sql("update venues set name = 'Old name' where source_id = 'node/21686579'")
    read_only = Database.connect(db_url, read_only=True)
    try:
        summary = run(read_only, dry_run=True, delete_floor=0.5)
    finally:
        read_only.close()
    assert (
        summary.counts.new,
        summary.counts.updated,
        summary.unchanged,
        summary.counts.deleted,
    ) == (0, 1, 4, 1)
    assert sql("select name from venues where source_id = 'node/21686579'") == [("Old name",)]
    assert "node/1" in stored(sql)
    assert sql("select count(*) from job_runs") == [(1,)]  # only the first, real run


def test_a_dry_run_refuses_a_writable_connection(db):
    with pytest.raises(ValueError, match="read-only"):
        run(db, dry_run=True)
