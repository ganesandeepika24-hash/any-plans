from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from jobs.normalise import Skipped
from jobs.sources import ticketmaster as tm
from jobs.windows import MIN_WINDOW, Window, initial_windows
from tests.conftest import LONDON, NOW, FakeTicketmaster, client_for, fixture

ONE_WEEK = [Window(NOW, NOW + timedelta(days=7))]


def parsed():
    return {
        e["id"]: tm.parse(e, LONDON.tz)
        for e in fixture("ticketmaster/events-page.json")["_embedded"]["events"]
    }


def fetch(server, windows=ONE_WEEK, **budget):
    client = client_for(server, **budget)
    return tm.fetch(client, "KEY", LONDON.lat, LONDON.lon, 25, windows), client


# Mapping ------------------------------------------------------------------------


def test_a_normal_event():
    e = parsed()["tm-jazz"]
    assert (e.title, e.category, e.genre, e.status) == (
        "Late Night Jazz",
        "Music",
        "jazz",
        "active",
    )
    assert e.start_at == datetime(2026, 11, 14, 19, 30, tzinfo=UTC)
    assert e.end_at is None
    assert (e.price_min, e.price_max, e.currency) == (Decimal(25), Decimal(65), "GBP")
    assert e.image_url.endswith("tm-jazz_1024.jpg")  # largest 16:9 no wider than 1,024 px
    assert (e.venue.source_id, e.venue.name, e.lat, e.lon) == (
        "KovZ9177Xx0",
        "Ronnie Scott's",
        51.51363,
        -0.13171,
    )


def test_no_specific_time_spans_the_local_day():
    e = parsed()["tm-no-time"]
    assert e.category == "Family"
    assert e.start_at == datetime(2026, 11, 20, tzinfo=UTC)
    assert e.end_at == datetime(2026, 11, 20, 23, 59, 59, tzinfo=UTC)


def test_multi_day_events_get_an_end_time():
    e = parsed()["tm-multi-day"]
    assert e.category == "Arts"
    assert e.end_at == datetime(2026, 12, 20, 23, 59, 59, tzinfo=UTC)


def test_cancelled_events_are_kept_as_cancelled():
    e = parsed()["tm-cancelled"]
    assert (e.category, e.status) == ("Comedy", "cancelled")


def test_postponed_events_are_skipped_and_marked_for_deletion():
    assert parsed()["tm-postponed"] == Skipped("postponed", "tm-postponed", delete=True)


def test_no_price_and_no_image():
    e = parsed()["tm-no-price"]
    assert (e.category, e.genre, e.price_min, e.price_max, e.image_url) == (
        "Sport",
        "football",
        None,
        None,
        None,
    )


def test_unmapped_segment_is_skipped():
    skipped = parsed()["tm-undefined"]
    assert isinstance(skipped, Skipped) and skipped.reason.startswith("unmapped category")


def test_inverted_price_range_is_dropped_not_the_event():
    e = parsed()["tm-inverted-price"]
    assert (e.category, e.price_min, e.price_max) == ("Theatre", None, None)


def test_other_genre_is_stored_as_none():
    assert parsed()["tm-ended"].genre is None


def test_summer_local_times_convert_from_bst():
    raw = fixture("ticketmaster/events-page.json")["_embedded"]["events"][0]
    raw["dates"]["start"] = {"localDate": "2026-07-01", "localTime": "19:30:00"}
    assert tm.parse(raw, LONDON.tz).start_at == datetime(2026, 7, 1, 18, 30, tzinfo=UTC)


# Fetching -----------------------------------------------------------------------


def test_request_parameters():
    server = FakeTicketmaster(lambda s, e: 0)
    fetch(server)
    p = server.requests[0]
    assert (p["countryCode"], p["radius"], p["unit"], p["size"], p["sort"]) == (
        "GB",
        "25",
        "km",
        "200",
        "date,asc",
    )
    assert p["geoPoint"].startswith("gcpvj")
    assert p["startDateTime"] == "2026-11-01T00:00:00Z"
    assert p["endDateTime"] == "2026-11-07T23:59:59Z"  # windows are [start, end)


def test_paging_uses_the_page_size_the_response_reports():
    server = FakeTicketmaster(lambda s, e: 45, applied_size=20)
    result, client = fetch(server)
    assert len(result.items) == 45
    assert client.budget.used == 3
    assert result.page_sizes == {20}


def test_paging_with_a_page_size_of_200():
    server = FakeTicketmaster(lambda s, e: 450, applied_size=200)
    result, client = fetch(server)
    assert (len(result.items), client.budget.used) == (450, 3)


def test_deep_paging_stops_at_1000_items_at_size_20():
    server = FakeTicketmaster(lambda s, e: 1000, applied_size=20)
    result, client = fetch(server)
    assert (len(result.items), client.budget.used) == (1000, 50)
    assert result.complete


def test_a_window_over_1000_items_is_split():
    hours = lambda s, e: int((e - s).total_seconds() // 3600) + 1  # noqa: E731
    server = FakeTicketmaster(lambda s, e: 9 * hours(s, e))  # 1,512 in a week
    result, client = fetch(server)
    assert result.split_windows == 1
    assert len(result.items) == 1512
    assert client.budget.used == 1 + 4 + 4  # one wasted call, then two halves of 756
    assert result.complete


def test_a_minimum_size_window_that_still_overflows_fails_the_window():
    w = Window(NOW, NOW + MIN_WINDOW)
    result, _ = fetch(FakeTicketmaster(lambda s, e: 1200), windows=[w])
    [failure] = result.failed_windows
    assert (failure.window, failure.status) == ("2026-11-01T00:00:00Z..2026-11-01T01:00:00Z", None)
    assert failure.message == "1200 items in a minimum-size window"
    assert len(result.items) == 1000
    assert not result.complete


def test_a_failing_window_does_not_stop_the_others():
    def server(request):
        if request.url.params["startDateTime"] == "2026-11-08T00:00:00Z":
            return httpx.Response(503)
        return FakeTicketmaster(lambda s, e: 5)(request)

    result, _ = fetch(server, windows=initial_windows(NOW))
    [failure] = result.failed_windows
    assert (failure.window, failure.status, failure.page) == (
        "2026-11-08T00:00:00Z..2026-11-15T00:00:00Z",
        503,
        0,
    )
    assert "Service Unavailable" in failure.message and "KEY" not in failure.message
    assert len(result.items) == 12 * 5
    assert not result.complete


def test_the_cap_stops_the_run_cleanly():
    result, client = fetch(
        FakeTicketmaster(lambda s, e: 5), windows=initial_windows(NOW), per_run=4
    )
    assert result.cap_reached == "per-run cap of 4 calls"
    assert len(result.items) == 4 * 5
    assert not result.complete


def test_production_caps():
    assert (tm.PER_RUN_CAP, tm.DAILY_STOP) == (2000, 4500)


def test_window_totals_and_results_are_recorded():
    result, _ = fetch(FakeTicketmaster(lambda s, e: 45, applied_size=20))
    assert result.windows == [
        {"window": "2026-11-01T00:00:00Z..2026-11-08T00:00:00Z", "total": 45, "results": 45}
    ]
