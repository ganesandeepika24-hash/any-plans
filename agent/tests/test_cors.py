"""CORS: only the origins in CORS_ORIGINS (or the optional regex) may call the agent from
a browser. Nothing set means no CORS headers at all."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.main import CORS_MAX_AGE_S, create_app

SITE = "https://anyplans.example.app"
LOVABLE = "https://any-plans.lovable.app"
OTHER = "https://evil.example.com"
ALLOW_ORIGIN = "access-control-allow-origin"


def client(monkeypatch, origins: str | None = None, regex: str | None = None) -> TestClient:
    for name, value in (("CORS_ORIGINS", origins), ("CORS_ORIGIN_REGEX", regex)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    return TestClient(create_app())


def preflight(c: TestClient, origin: str, path: str = "/search"):
    return c.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


# Allowed and refused origins ------------------------------------------------------


@pytest.mark.parametrize("origin", [SITE, LOVABLE])
def test_a_listed_origin_passes_the_preflight(monkeypatch, origin):
    with client(monkeypatch, f"{SITE}, {LOVABLE}") as c:
        r = preflight(c, origin)
    assert r.status_code == 200
    assert r.headers[ALLOW_ORIGIN] == origin  # the origin itself, never "*"
    assert set(r.headers["access-control-allow-methods"].split(", ")) == {"GET", "POST"}
    assert "authorization" in r.headers["access-control-allow-headers"].lower()
    assert r.headers["access-control-max-age"] == str(CORS_MAX_AGE_S)
    assert "access-control-allow-credentials" not in r.headers


@pytest.mark.parametrize(
    "origin",
    [
        OTHER,
        "http://anyplans.example.app",  # the site's address over plain http
        "https://anyplans.example.app.evil.com",  # a longer host that starts the same
        "https://preview--any-plans.lovable.app",  # a preview: exact addresses only
    ],
)
def test_an_unlisted_origin_gets_no_allow_header(monkeypatch, origin):
    with client(monkeypatch, f"{SITE},{LOVABLE}") as c:
        r = preflight(c, origin)
        post = c.post("/parse", json={"query": "jazz"}, headers={"Origin": origin})
    assert r.status_code == 400 and ALLOW_ORIGIN not in r.headers
    assert ALLOW_ORIGIN not in post.headers


def test_nothing_set_means_no_browser_can_call(monkeypatch):
    with client(monkeypatch) as c:
        r = preflight(c, SITE)
        health = c.get("/health", headers={"Origin": SITE})
    assert ALLOW_ORIGIN not in r.headers
    assert health.status_code == 200 and ALLOW_ORIGIN not in health.headers


def test_a_401_carries_the_header_so_the_app_can_read_it(monkeypatch):
    with client(monkeypatch, SITE) as c:
        r = c.post("/parse", json={"query": "jazz"}, headers={"Origin": SITE})
    assert r.status_code == 401
    assert r.headers[ALLOW_ORIGIN] == SITE


def test_health_carries_the_header(monkeypatch):
    with client(monkeypatch, SITE) as c:
        r = c.get("/health", headers={"Origin": SITE})
    assert r.headers[ALLOW_ORIGIN] == SITE


def test_put_is_not_an_allowed_method(monkeypatch):
    with client(monkeypatch, SITE) as c:
        r = c.options("/search", headers={"Origin": SITE, "Access-Control-Request-Method": "PUT"})
    assert r.status_code == 400


# The optional preview pattern (option b; unset in production) -----------------------


def test_the_regex_allows_matching_previews_only(monkeypatch):
    regex = r"https://any-plans-[a-z0-9-]+\.vercel\.app"
    with client(monkeypatch, SITE, regex) as c:
        ok = preflight(c, "https://any-plans-git-feat-x.vercel.app")
        other = preflight(c, "https://someone-else.vercel.app")
        suffix = preflight(c, "https://any-plans-x.vercel.app.evil.com")
    assert ok.headers[ALLOW_ORIGIN] == "https://any-plans-git-feat-x.vercel.app"
    assert ALLOW_ORIGIN not in other.headers
    assert ALLOW_ORIGIN not in suffix.headers  # the whole origin must match


def test_a_blank_regex_is_unset(monkeypatch):
    assert Settings(cors_origin_regex="  ").cors_origin_regex is None


# Settings refused at startup ------------------------------------------------------


@pytest.mark.parametrize(
    "origins",
    [
        "*",
        "http://anyplans.example.app",
        "https://anyplans.example.app/",
        "https://anyplans.example.app/app",
        "https://*.lovable.app",
        "https://AnyPlans.example.app",  # browsers send lower case: it could never match
        "anyplans.example.app",
        f"{SITE},*",
    ],
)
def test_a_bad_origin_is_refused_at_startup(monkeypatch, origins):
    monkeypatch.setenv("CORS_ORIGINS", origins)
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="CORS origin"):
        create_app()


@pytest.mark.parametrize("origins", ["http://localhost:8080", "http://127.0.0.1:5173"])
def test_local_development_may_use_http(origins):
    assert Settings(cors_origins=origins).cors_origin_list == [origins]


@pytest.mark.parametrize("regex", [".*", r"http://.*\.vercel\.app", "https://(unclosed"])
def test_a_bad_regex_is_refused(regex):
    with pytest.raises(ValidationError):
        Settings(cors_origin_regex=regex)


def test_origins_are_split_and_trimmed():
    settings = Settings(cors_origins=f" {SITE} ,, {LOVABLE},")
    assert settings.cors_origin_list == [SITE, LOVABLE]
