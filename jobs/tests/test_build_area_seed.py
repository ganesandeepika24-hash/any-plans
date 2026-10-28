"""The area seed builder (decision 0021): ONS boroughs, OSM places, hand-picked areas.

Fixtures:
- tests/fixtures/ons-london-boroughs.json: ONS's reply for the 33 London boroughs
  (Local Authority Districts, December 2024), fetched 28 Oct 2026. Source: Office for
  National Statistics licensed under the Open Government Licence v.3.0. Contains OS data
  © Crown copyright and database right 2024.
- tests/fixtures/osm/city-of-london-places.osm: the venues fixture (City of London's
  boundary, relation 51800, standing in for Greater London), plus real place nodes from
  Geofabrik's extract of 27 Oct 2026 (© OpenStreetMap contributors, ODbL):
  - inside: Temple, Aldgate, Barbican (quarters), West Smithfield (neighbourhood), and
    node/12286269903, Liverpool Street station
  - inside but not kept: King's Reach (a locality), the City of London (a city)
  - outside: Clerkenwell (suburb), Spitalfields (quarter)

The clash rules are tested on hand-written places, not the fixture.
"""

import ast
import json
from pathlib import Path

import pytest

from jobs import build_area_seed as seed
from jobs.build_area_seed import Borough, SeedError, build, normalise, parse_boroughs, read_osm

FIXTURES = Path(__file__).parent / "fixtures"
ONS = json.loads((FIXTURES / "ons-london-boroughs.json").read_text())
OSM = FIXTURES / "osm" / "city-of-london-places.osm"
CITY = 51800
STATION = "node/12286269903"
REPO = Path(__file__).resolve().parents[2]


# Inputs ---------------------------------------------------------------------------


def test_ons_gives_the_33_boroughs_in_code_order():
    boroughs = parse_boroughs(ONS)
    assert len(boroughs) == 33
    assert boroughs[0] == Borough("E09000001", "City of London", 51.51564, -0.09352)
    assert [b.code for b in boroughs] == [f"E09{n:06d}" for n in range(1, 34)]


@pytest.mark.parametrize(
    "body",
    [
        {"features": ONS["features"][:32]},  # one missing
        {"features": ONS["features"] + ONS["features"][:1]},  # a repeat
        {
            "features": [
                {"attributes": ONS["features"][0]["attributes"] | {"LAD24CD": "E06000001"}}
            ]
            + ONS["features"][1:]
        },  # not a London code
        {"features": [{"attributes": {"LAD24CD": "E09000001"}}]},  # no name or point
        {"error": {"code": 400}},  # ArcGIS's error shape
    ],
)
def test_an_ons_reply_that_isnt_the_33_boroughs_is_refused(body):
    with pytest.raises(SeedError):
        parse_boroughs(body)


def test_the_extract_gives_named_places_inside_the_boundary_and_the_landmarks():
    places, landmarks = read_osm(OSM, {STATION: "Liverpool Street"}, CITY)
    assert sorted((p[1], p[2]) for p in places) == [
        ("Aldgate", "quarter"),
        ("Barbican", "quarter"),
        ("Temple", "quarter"),
        ("West Smithfield", "neighbourhood"),
    ]
    assert landmarks == {STATION: pytest.approx((51.5176474, -0.0864414))}


def test_a_landmark_missing_from_the_extract_is_an_error():
    with pytest.raises(SeedError, match="node/1"):
        read_osm(OSM, {STATION: "Liverpool Street", "node/1": "Nowhere"}, CITY)


# Building -------------------------------------------------------------------------

BOROUGHS = [Borough("E09000001", "City of London", 51.51, -0.09)]
CURATION = {
    "boroughs": {"london-city-of-london": {"aliases": ["The City", "Square Mile"]}},
    "neighbourhoods": [
        {
            "id": "london-soho",
            "name": "Soho",
            "aliases": ["Chinatown"],
            "lat": 51.51,
            "lon": -0.13,
            "osm": "relation/17710512",
        },
    ],
    "landmarks": [
        {
            "id": "london-liverpool-street",
            "name": "Liverpool Street",
            "aliases": ["Liverpool Street station"],
            "osm": STATION,
        },
    ],
}
LANDMARKS = {STATION: (51.5177, -0.0864)}


def place(n, name, kind):
    return (f"node/{n}", name, kind, 51.5, -0.1)


def built(places):
    return build(BOROUGHS, CURATION, places, LANDMARKS)


def names(b):
    return {r.name: (r.kind, r.source) for r in b.rows}


def test_boroughs_get_their_code_and_the_hand_picked_rows_keep_their_points():
    rows = {r.id: r for r in built([]).rows}
    city = rows["london-city-of-london"]
    assert (city.kind, city.gss_code, city.radius_km, city.aliases) == (
        "borough",
        "E09000001",
        6,
        ("The City", "Square Mile"),
    )
    soho = rows["london-soho"]
    assert (soho.lat, soho.lon, soho.radius_km, soho.gss_code) == (51.51, -0.13, 1, None)
    station = rows["london-liverpool-street"]
    assert (station.kind, station.lat, station.radius_km) == ("landmark", 51.5177, 1)


def test_an_earlier_area_keeps_its_name_and_aliases():
    # OSM has its own Chinatown and Soho; the hand-picked Soho and its alias win.
    b = built(
        [
            place(1, "Chinatown", "suburb"),
            place(2, "Soho", "suburb"),
            place(3, "Square Mile", "quarter"),
            place(4, "Aldgate", "quarter"),
        ]
    )
    assert sorted(b.taken) == ["Chinatown", "Soho", "Square Mile"]
    assert names(b)["Aldgate"] == ("neighbourhood", "node/4 (quarter)")
    assert "london-chinatown" not in {r.id for r in b.rows}


def test_london_and_central_london_are_never_areas():
    b = built([place(1, "London", "town"), place(2, "Central London", "quarter")])
    assert sorted(b.taken) == ["Central London", "London"]


def test_a_higher_rank_keeps_a_shared_name():
    b = built([place(1, "Hampstead", "town"), place(2, "Hampstead", "suburb")])
    assert names(b)["Hampstead"] == ("neighbourhood", "node/1 (town)")
    assert b.duplicates == {}


def test_two_places_of_the_same_rank_with_one_name_are_both_left_out():
    # Hayes (Hillingdon and Bromley) is the real case; neighbourhood and quarter rank equal.
    b = built(
        [
            place(1, "Hayes", "town"),
            place(2, "Hayes", "town"),
            place(3, "Plaistow", "neighbourhood"),
            place(4, "Plaistow", "quarter"),
        ]
    )
    assert "Hayes" not in names(b) and "Plaistow" not in names(b)
    assert b.duplicates == {"Hayes": ["node/1", "node/2"], "Plaistow": ["node/3", "node/4"]}


def test_names_that_differ_only_in_punctuation_are_one_name():
    b = built([place(1, "St. John's Wood", "suburb"), place(2, "St Johns Wood", "suburb")])
    assert b.duplicates == {"St. John's Wood": ["node/1", "node/2"]}


def test_an_ons_borough_with_no_curation_entry_is_an_error():
    with pytest.raises(SeedError, match="Narnia"):
        build(BOROUGHS + [Borough("E09000002", "Narnia", 51, 0)], CURATION, [], LANDMARKS)


def test_a_curation_borough_ons_lacks_is_an_error():
    curation = CURATION | {"boroughs": CURATION["boroughs"] | {"london-narnia": {"aliases": []}}}
    with pytest.raises(SeedError, match="ONS doesn't have"):
        build(BOROUGHS, curation, [], LANDMARKS)


def test_an_osm_place_that_would_reuse_an_id_is_an_error():
    # A hand-picked id needn't be its name's slug; an OSM name that slugs to it is refused.
    curation = CURATION | {
        "neighbourhoods": [
            {
                "id": "london-cc",
                "name": "City Centre",
                "aliases": [],
                "lat": 51.5,
                "lon": -0.1,
                "osm": "node/9",
            }
        ]
    }
    with pytest.raises(SeedError, match="london-cc"):
        build(BOROUGHS, curation, [place(1, "CC", "quarter")], LANDMARKS)


# Output ---------------------------------------------------------------------------


def test_render_writes_one_row_per_area_with_its_source():
    sql = seed.render(built([place(4, "Aldgate", "quarter")]), "a test extract")
    assert "'SRID=4326;POINT(-0.090000 51.510000)', 6, 'E09000001'),  -- ONS E09000001" in sql
    assert "('london-aldgate', 'Aldgate', 'neighbourhood', '{}'::text[]" in sql
    assert sql.rstrip().endswith("gss_code          = excluded.gss_code;")
    assert "Open Government Licence v.3.0" in sql and "© OpenStreetMap contributors" in sql


def test_the_committed_seed_matches_the_committed_curation():
    # Edits go in the curation, then the script reruns: the two must agree.
    curation = json.loads(seed.CURATION.read_text())
    sql = seed.SEED.read_text()
    for area_id in curation["boroughs"]:
        assert f"({_q(area_id)}, " in sql, area_id
    for n in curation["neighbourhoods"]:
        assert f"({_q(n['id'])}, {_q(n['name'])}, 'neighbourhood', " in sql, n["id"]
        assert f"POINT({n['lon']:.6f} {n['lat']:.6f})" in sql, n["id"]
    for lm in curation["landmarks"]:
        assert f"({_q(lm['id'])}, {_q(lm['name'])}, 'landmark', " in sql, lm["id"]
    assert sql.count(", 'borough', ") == 33 and sql.count("'E09") == 33


def _q(text):
    return "'" + text.replace("'", "''") + "'"


def test_names_are_matched_as_the_agent_matches_them():
    # The agent's normalise decides what a user's place matches; the seed's clash check
    # must use the same one.
    def source(path, name):
        tree = ast.parse(path.read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        fn.body = [
            s
            for s in fn.body
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
        ]  # docstrings may differ
        return ast.dump(fn)

    agent = REPO / "agent" / "app" / "understand" / "resolve.py"
    assert source(Path(seed.__file__), "normalise") == source(agent, "normalise")
    assert normalise("The King's Cross") == "kings cross"
