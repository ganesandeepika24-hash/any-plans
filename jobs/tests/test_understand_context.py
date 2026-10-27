"""understand_context (migration 011, decision 0012): the city and area points the agent
resolves place names with. Needs TEST_DATABASE_URL with the seeds loaded."""

import psycopg
import pytest

pytestmark = pytest.mark.db


def context(db_url, city_id="london"):
    with psycopg.connect(db_url, autocommit=True) as conn:
        return conn.execute("select public.understand_context(%s)", (city_id,)).fetchone()[0]


def test_returns_the_city_and_every_seeded_area_with_coordinates(db_url, sql):
    got = context(db_url)
    assert got["city"] == {"id": "london", "name": "London", "timezone": "Europe/London"}
    assert len(got["areas"]) == sql("select count(*) from areas where city_id = 'london'")[0][0]
    soho = next(a for a in got["areas"] if a["id"] == "london-soho")
    lat, lon = sql(
        "select extensions.st_y(centre_geom::extensions.geometry),"
        " extensions.st_x(centre_geom::extensions.geometry)"
        " from areas where id = 'london-soho'"
    )[0]
    assert (soho["lat"], soho["lon"]) == (pytest.approx(lat), pytest.approx(lon))
    assert soho["default_radius_km"] == 1 and soho["aliases"] == []
    angel = next(a for a in got["areas"] if a["id"] == "london-angel")
    assert angel["aliases"] == ["The Angel"]


def test_an_unknown_city_gives_null(db_url):
    assert context(db_url, "atlantis") is None


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_only_service_role_may_call_it(db_url, role):
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(f"set role {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select public.understand_context('london')")
        conn.execute("set role service_role")
        got = conn.execute("select public.understand_context('london')").fetchone()[0]
    assert got["city"]["id"] == "london"


def test_the_understand_config_validates_in_shape(sql):
    rows = dict(
        sql(
            "select key, value_json from config"
            " where key in ('llm_understand', 'understand_prompt') and is_active"
        )
    )
    providers = rows["llm_understand"]["providers"]
    assert [p["provider"] for p in providers] == ["groq", "cloudflare"]
    assert [p["model"] for p in providers] == ["openai/gpt-oss-20b", "@cf/openai/gpt-oss-20b"]
    assert rows["understand_prompt"]["system"].startswith("You turn a search")
