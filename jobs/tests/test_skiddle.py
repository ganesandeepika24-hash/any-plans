from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from jobs.normalise import Skipped
from jobs.sources import skiddle as sk
from tests.conftest import LONDON, client_for, fixture


def parsed():
    return {e["id"]: sk.parse(e, LONDON.tz) for e in fixture("skiddle/events-page.json")["results"]}


def test_a_normal_event():
    e = parsed()["sk-live"]
    assert (e.title, e.category, e.genre, e.status) == ("Soul Night", "Music", "soul", "active")
    assert (e.start_at, e.end_at) == (
        datetime(2026, 11, 7, 19, 30, tzinfo=UTC),
        datetime(2026, 11, 7, 23, tzinfo=UTC),
    )
    assert (e.price_min, e.price_max, e.currency) == (Decimal(15), Decimal(15), "GBP")
    assert e.venue.name == "Jazz Cafe" and e.venue.address == "5 Parkway, London, NW1 7PG"


def test_codes_and_flags():
    p = parsed()
    assert (p["sk-club"].category, p["sk-club"].status) == ("Nightlife", "cancelled")
    assert p["sk-free"].category == "Comedy"
    assert p["sk-range"].category == "Theatre"
    assert p["sk-festival"].category == "Music"
    assert p["sk-festival"].end_at == datetime(2026, 11, 15, 23, tzinfo=UTC)
    assert isinstance(p["sk-unmapped"], Skipped)


def test_times_without_an_offset_are_london_local():
    raw = fixture("skiddle/events-page.json")["results"][0]
    raw["startdate"], raw["enddate"] = "2026-07-01T19:30:00", None
    assert sk.parse(raw, LONDON.tz).start_at == datetime(2026, 7, 1, 18, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("£15.00", (Decimal(15), Decimal(15))),
        ("£10.00 - £25.00", (Decimal(10), Decimal(25))),
        ("Free", (Decimal(0), Decimal(0))),
        ("", (None, None)),
        (None, (None, None)),
        ("Pay what you can", (None, None)),
    ],
)
def test_price_parsing(text, expected):
    assert sk.parse_price(text) == expected


def test_paging_advances_by_what_each_page_returns():
    offsets = []

    def server(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        count = max(0, min(40, 95 - offset))  # the server returns 40 a page, not the 100 asked
        results = [{"id": offset + i} for i in range(count)]
        return httpx.Response(200, json={"error": 0, "totalcount": "95", "results": results})

    client = client_for(server)
    result = sk.fetch(
        client, "KEY", LONDON.lat, LONDON.lon, 25, date(2026, 11, 1), date(2027, 1, 30)
    )
    assert offsets == [0, 40, 80]
    assert len(result.items) == 95 and result.complete


def test_radius_is_rounded_up_to_whole_miles():
    seen = {}

    def server(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"error": 0, "totalcount": "0", "results": []})

    sk.fetch(
        client_for(server), "KEY", LONDON.lat, LONDON.lon, 25, date(2026, 11, 1), date(2027, 1, 30)
    )
    assert (seen["radius"], seen["limit"], seen["minDate"]) == ("16", "100", "2026-11-01")
