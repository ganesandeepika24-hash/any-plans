"""Skiddle mapping against real events (trimmed from dry run 40386725066), and fetching."""

import copy
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from jobs.normalise import Skipped
from jobs.sources import skiddle as sk
from tests.conftest import LONDON, client_for, fixture

EVENTS = fixture("skiddle/events-page.json")["results"]
BY_ID = {str(e["id"]): e for e in EVENTS}


def by_code(code: str) -> dict:
    return next(e for e in EVENTS if (e["EventCode"] or "") == code)


def parse(event_id: int):
    return sk.parse(BY_ID[str(event_id)], LONDON.tz)


# Mapping: one test for every code seen in the dry run --------------------------

MAPPED = {
    "LIVE": "Music",
    "FEST": "Music",
    "CLUB": "Nightlife",
    "BARPUB": "Nightlife",
    "DATE": "Nightlife",
    "GAMING": "Nightlife",
    "DANCE": "Nightlife",
    "SEASONAL": "Nightlife",
    "COMEDY": "Comedy",
    "THEATRE": "Theatre",
    "EXHIB": "Arts",
    "ARTS": "Arts",
    "FILM": "Arts",
    "BOOKS": "Arts",
    "KIDS": "Family",
    "SPORT": "Sport",
    "FOOD": "Food & Drink",
}
SKIPPED = ["TOUR", "EDU", "EXPER", "NET", "HEALTH", "CONF", "RELIG", "HOBBY", "AWARD", "FUND", ""]


@pytest.mark.parametrize(("code", "category"), MAPPED.items())
def test_mapped_code(code, category):
    event = sk.parse(by_code(code), LONDON.tz)
    assert event.category == category


@pytest.mark.parametrize("code", SKIPPED, ids=[c or "empty" for c in SKIPPED])
def test_skipped_code(code):
    skipped = sk.parse(by_code(code), LONDON.tz)
    assert skipped == Skipped(f"skipped event code ({code or 'empty'})", str(by_code(code)["id"]))


def test_every_code_in_the_fixture_is_decided():
    assert {e["EventCode"] or "" for e in EVENTS} == set(MAPPED) | set(SKIPPED)


def test_a_code_nobody_has_reviewed_is_reported_as_unmapped():
    event = copy.deepcopy(by_code("LIVE"))
    event["EventCode"] = "NEWCODE"
    assert sk.parse(event, LONDON.tz).reason == "unmapped event code (NEWCODE)"


# Fields -------------------------------------------------------------------------


def test_a_real_event():
    e = parse(42623375)  # "Sheer Terror", LIVE
    assert (e.title, e.category, e.genre, e.status) == ("Sheer Terror", "Music", None, "active")
    assert e.start_at == datetime(2026, 11, 2, 18, tzinfo=UTC)
    assert e.end_at == datetime(2026, 11, 2, 23, tzinfo=UTC)
    assert e.venue.source_id and e.lat and e.lon
    assert e.url.startswith("https://www.skiddle.com/")


def test_genre_is_always_null():
    assert {sk.parse(e, LONDON.tz).genre for e in EVENTS if (e["EventCode"] or "") in MAPPED} == {
        None
    }


def test_cancelled():
    assert parse(42698174).status == "cancelled"


def test_multi_day_festival_keeps_its_end():
    e = parse(42262752)  # EFG London Jazz Festival
    assert e.end_at == datetime(2026, 11, 22, 23, tzinfo=UTC)


# Prices: entryprice first, then ticketpricing; 0/0 and "0" are unknown --------


@pytest.mark.parametrize(
    ("event_id", "low", "high"),
    [
        (42504394, "30.00", "210.00"),  # "£30.00 - £210.00"; ticketpricing says 6.5-8.5
        (43014964, "55.00", "55.00"),  # "55.00"
        (42332455, "25.00", "25.00"),  # "General Admission: GBP 25.00"
        (41995948, "39.50", "89.50"),  # "GBP 39.50 - GBP 89.50"
        (41992305, "5", "40"),  # "5-40"
        (42803796, "8", "8"),  # no entryprice; ticketpricing 8/8
    ],
)
def test_prices(event_id, low, high):
    e = parse(event_id)
    assert (e.price_min, e.price_max, e.currency) == (Decimal(low), Decimal(high), "GBP")


def test_ticketpricing_zero_zero_is_unknown_not_free():
    e = parse(43018746)
    assert (e.price_min, e.price_max, e.currency) == (None, None, None)


def test_entryprice_zero_is_unknown_not_free():
    e = parse(42262752)  # EFG London Jazz Festival: entryprice "0"
    assert (e.price_min, e.price_max) == (None, None)


def test_no_price_fields_at_all():
    assert sk.prices(BY_ID["42540877"]) == (None, None)


def test_free_means_free():
    assert sk.parse_price("Free") == (Decimal(0), Decimal(0))


def test_the_events_own_currency_is_used():
    # The only EUR event in the sample is a food tour (TOUR, skipped), so this is a
    # copy of it with a mapped code.
    event = copy.deepcopy(BY_ID["42734032"])
    event["EventCode"] = "FOOD"
    e = sk.parse(event, LONDON.tz)
    assert (e.price_min, e.price_max, e.currency) == (Decimal(292), Decimal(292), "EUR")


def test_times_without_an_offset_are_london_local():
    event = copy.deepcopy(by_code("LIVE"))
    event["startdate"], event["enddate"] = "2026-07-01T19:30:00", None
    assert sk.parse(event, LONDON.tz).start_at == datetime(2026, 7, 1, 18, 30, tzinfo=UTC)


# Windows ------------------------------------------------------------------------


def test_date_windows_share_their_boundary_days():
    windows = sk.date_windows(date(2026, 11, 1), date(2027, 1, 30))
    assert len(windows) == 13
    assert windows[0].start == date(2026, 11, 1) and windows[-1].end == date(2027, 1, 30)
    assert all(a.end == b.start for a, b in zip(windows, windows[1:], strict=False))


def test_split_and_minimum():
    left, right = sk.DateWindow(date(2026, 11, 1), date(2026, 11, 8)).split()
    assert (left.end, right.start) == (date(2026, 11, 4), date(2026, 11, 4))
    assert not sk.DateWindow(date(2026, 11, 1), date(2026, 11, 2)).can_split()


class FakeSkiddle:
    """Synthetic results: `per_day` events for each day in [minDate, maxDate), served
    `page` at a time, failing past the 10,000th result as the real API did."""

    def __init__(self, per_day, page=100, fail=None):
        self.per_day, self.page, self.fail = per_day, page, fail
        self.requests: list[dict] = []

    def __call__(self, request):
        p = dict(request.url.params)
        self.requests.append(p)
        if self.fail and self.fail(p):
            return httpx.Response(503, text="upstream unavailable")
        offset = int(p["offset"])
        if offset >= 10_000:
            return httpx.Response(500, text="Result window is too large")
        days = (date.fromisoformat(p["maxDate"]) - date.fromisoformat(p["minDate"])).days
        total = self.per_day * days
        count = max(0, min(self.page, total - offset))
        results = [{"id": f"{p['minDate']}-{offset + i}"} for i in range(count)]
        return httpx.Response(
            200,
            json={
                "error": 0,
                "totalcount": str(total),
                "pagecount": str(count),
                "results": results,
            },
        )


def fetch(server, windows, **budget):
    return sk.fetch(client_for(server, **budget), "KEY", LONDON.lat, LONDON.lon, 25, windows)


WEEK = [sk.DateWindow(date(2026, 11, 1), date(2026, 11, 8))]


def test_request_parameters():
    server = FakeSkiddle(per_day=0)
    fetch(server, WEEK)
    p = server.requests[0]
    assert (p["radius"], p["limit"], p["minDate"], p["maxDate"], p["offset"]) == (
        "16",
        "100",
        "2026-11-01",
        "2026-11-08",
        "0",
    )


def test_paging_advances_by_what_each_page_returns():
    server = FakeSkiddle(per_day=10, page=40)  # 70 results, 40 a page
    result = fetch(server, WEEK)
    assert [r["offset"] for r in server.requests] == ["0", "40"]
    assert len(result.items) == 70 and result.complete


def test_windows_and_pages_are_recorded():
    result = fetch(FakeSkiddle(per_day=30), WEEK)  # 210 results
    assert result.windows == [
        {
            "window": "2026-11-01..2026-11-08",
            "totalcount": 210,
            "results": 210,
            "pages": [
                {"offset": 0, "results": 100, "pagecount": "100"},
                {"offset": 100, "results": 100, "pagecount": "100"},
                {"offset": 200, "results": 10, "pagecount": "10"},
            ],
        }
    ]
    assert result.page_sizes == {100, 10}


def test_a_window_over_10000_results_is_split():
    result = fetch(FakeSkiddle(per_day=1500), WEEK)  # 10,500 in the week
    assert result.split_windows == 1
    assert [w["window"] for w in result.windows] == [
        "2026-11-01..2026-11-08",
        "2026-11-01..2026-11-04",
        "2026-11-04..2026-11-08",
    ]
    assert result.windows[0]["split"] is True
    assert len(result.items) == 4500 + 6000
    assert result.complete


def test_a_one_day_window_over_10000_fetches_10000_and_fails_without_asking_for_more():
    server = FakeSkiddle(per_day=12_000)
    day = [sk.DateWindow(date(2026, 11, 1), date(2026, 11, 2))]
    result = fetch(server, day)
    assert len(result.items) == 10_000
    assert max(int(r["offset"]) for r in server.requests) == 9_900
    assert [(f.window, f.status) for f in result.failed_windows] == [
        ("2026-11-01..2026-11-02", None)
    ]
    assert "12000 results" in result.failed_windows[0].message
    assert not result.complete


def test_a_failed_request_records_window_status_message_and_offset():
    server = FakeSkiddle(
        per_day=30, fail=lambda p: p["minDate"] == "2026-11-08" and p["offset"] == "100"
    )
    windows = sk.date_windows(date(2026, 11, 1), date(2026, 11, 22))
    result = fetch(server, windows)
    [failure] = result.failed_windows
    assert (failure.window, failure.status, failure.page) == ("2026-11-08..2026-11-15", 503, 100)
    assert "Service Unavailable" in failure.message and "upstream unavailable" in failure.message
    assert "KEY" not in failure.message
    assert len(result.items) == 210 + 100 + 210  # the other windows still ran
    assert not result.complete


def test_a_skiddle_error_body_is_a_failure():
    def server(request):
        return httpx.Response(200, json={"error": 1, "errormessage": "Invalid date range"})

    [failure] = fetch(server, WEEK).failed_windows
    assert (failure.status, failure.page) == (200, 0)
    assert "Invalid date range" in failure.message


def test_the_cap_stops_cleanly():
    windows = sk.date_windows(date(2026, 11, 1), date(2027, 1, 30))
    result = fetch(FakeSkiddle(per_day=1), windows, per_run=3)
    assert result.cap_reached == "per-run cap of 3 calls"
    assert not result.complete


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("£54.00 - £66.00", (Decimal("54.00"), Decimal("66.00"))),
        ("65", (Decimal(65), Decimal(65))),
        ("Free", (Decimal(0), Decimal(0))),
        ("", (None, None)),
        (None, (None, None)),
        ("Pay what you can", (None, None)),
    ],
)
def test_parse_price(text, expected):
    assert sk.parse_price(text) == expected
