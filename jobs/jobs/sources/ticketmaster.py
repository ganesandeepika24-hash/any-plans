"""Ticketmaster Discovery API: fetch London events by date window and map them.

Ticketmaster's terms are NOT CLEARED for a scheduled bulk fetch (decision 0001),
so this source only runs on manual dispatch. Images are linked, never copied.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from jobs.geo import geohash
from jobs.http import CapReached, RateLimitedClient, RequestFailed
from jobs.normalise import Category, Event, Skipped, Venue, end_of_local_day, local_start
from jobs.windows import Window

SOURCE = "ticketmaster"
URL = "https://app.ticketmaster.com/discovery/v2/events.json"
MIN_INTERVAL = 0.5  # 2 requests a second (developer FAQ)
PER_RUN_CAP = 2000
DAILY_STOP = 4500  # of the 5,000-call daily quota

# A request, not an assumption: Ticketmaster documents no maximum, so paging uses
# the page size each response reports.
REQUESTED_SIZE = 200
# "We only support retrieving the 1000th item, i.e. size * page < 1000."
DEEP_PAGING_LIMIT = 1000

ARTS_AND_THEATRE: dict[str, Category] = {
    "Comedy": "Comedy",
    "Theatre": "Theatre",
    "Musical": "Theatre",
    "Opera": "Theatre",
    "Children's Theatre": "Family",
}
MISCELLANEOUS: dict[str, Category] = {"Family": "Family", "Food & Drink": "Food & Drink"}
NO_GENRE = {"Undefined", "Other"}
MAX_IMAGE_WIDTH = 1024


@dataclass
class FetchResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    failed_windows: list[Window] = field(default_factory=list)
    split_windows: int = 0
    page_sizes: set[int] = field(default_factory=set)
    cap_reached: str | None = None

    @property
    def complete(self) -> bool:
        return not self.failed_windows and self.cap_reached is None


def fetch(
    client: RateLimitedClient,
    api_key: str,
    lat: float,
    lon: float,
    radius_km: float,
    windows: list[Window],
    on_page: Callable[[Window, int, Any], None] | None = None,
) -> FetchResult:
    result = FetchResult()
    pending = list(windows)
    try:
        while pending:
            window = pending.pop(0)
            try:
                _fetch_window(
                    client, api_key, lat, lon, radius_km, window, pending, result, on_page
                )
            except RequestFailed:
                result.failed_windows.append(window)
    except CapReached as cap:
        result.cap_reached = cap.reason
    return result


def _fetch_window(client, api_key, lat, lon, radius_km, window, pending, result, on_page) -> None:
    params = {
        "apikey": api_key,
        "countryCode": "GB",
        "geoPoint": geohash(lat, lon),
        "radius": f"{radius_km:g}",
        "unit": "km",
        "startDateTime": _api_time(window.start),
        # Windows are [start, end); the API takes whole seconds.
        "endDateTime": _api_time(window.end.replace(microsecond=0) - timedelta(seconds=1)),
        "size": REQUESTED_SIZE,
        "sort": "date,asc",
        "page": 0,
    }
    first = client.get_json(URL, params)
    if on_page:
        on_page(window, 0, first)
    total = int(first["page"]["totalElements"])
    size = int(first["page"]["size"])
    result.page_sizes.add(size)

    if total > DEEP_PAGING_LIMIT and window.can_split():
        pending[:0] = window.split()
        result.split_windows += 1
        return
    if total > DEEP_PAGING_LIMIT:
        # Can't split further, so this window can't be fetched in full.
        result.failed_windows.append(window)

    result.items.extend(_events(first))
    total_pages = int(first["page"]["totalPages"])
    page = 1
    while page < total_pages and size * page < DEEP_PAGING_LIMIT:
        body = client.get_json(URL, {**params, "page": page})
        if on_page:
            on_page(window, page, body)
        result.page_sizes.add(int(body["page"]["size"]))
        result.items.extend(_events(body))
        page += 1


def _events(body: dict[str, Any]) -> list[dict[str, Any]]:
    return body.get("_embedded", {}).get("events", [])


def parse(raw: dict[str, Any], tz: ZoneInfo) -> Event | Skipped:
    source_id = raw.get("id")
    dates = raw.get("dates", {})
    status = dates.get("status", {}).get("code")
    if status == "postponed":
        return Skipped("postponed", source_id, delete=True)

    start = dates.get("start", {})
    if (
        start.get("dateTBD")
        or start.get("dateTBA")
        or not (start.get("dateTime") or start.get("localDate"))
    ):
        return Skipped("no date", source_id)
    whole_day = bool(start.get("timeTBA") or start.get("noSpecificTime"))
    start_at = _when(start, tz, whole_day)
    start_day = date.fromisoformat(start["localDate"]) if start.get("localDate") else None

    end = dates.get("end") or {}
    if end.get("dateTime") or (end.get("localDate") and end.get("localTime")):
        end_at = _when(end, tz, whole_day=False)
    elif end.get("localDate"):
        end_at = end_of_local_day(date.fromisoformat(end["localDate"]), tz)
    elif whole_day and start_day:
        end_at = end_of_local_day(start_day, tz)
    else:
        end_at = None

    classification = (raw.get("classifications") or [{}])[0]
    segment = classification.get("segment", {}).get("name")
    genre = classification.get("genre", {}).get("name")
    category = _category(segment, genre)
    if category is None:
        return Skipped(f"unmapped category ({segment} / {genre})", source_id)

    price_min, price_max, currency = _prices(raw.get("priceRanges") or [])
    venue_raw = (raw.get("_embedded", {}).get("venues") or [None])[0]
    venue = _venue(venue_raw) if venue_raw else None

    return Event(
        source=SOURCE,
        source_id=source_id,
        title=raw["name"],
        category=category,
        genre=genre.lower() if genre and genre not in NO_GENRE else None,
        start_at=start_at,
        end_at=end_at,
        price_min=price_min,
        price_max=price_max,
        currency=currency,
        url=raw.get("url"),
        image_url=_image(raw.get("images") or []),
        lat=venue.lat if venue else None,
        lon=venue.lon if venue else None,
        status="cancelled" if status == "cancelled" else "active",
        venue=venue,
    )


def _category(segment: str | None, genre: str | None) -> Category | None:
    if segment == "Music":
        return "Music"
    if segment == "Sports":
        return "Sport"
    if segment == "Film":
        return "Arts"
    if segment == "Arts & Theatre":
        return ARTS_AND_THEATRE.get(genre or "", "Arts")
    if segment == "Miscellaneous":
        return MISCELLANEOUS.get(genre or "")
    return None


def _when(part: dict[str, Any], tz: ZoneInfo, whole_day: bool) -> datetime:
    if part.get("dateTime") and not whole_day:
        return datetime.fromisoformat(part["dateTime"].replace("Z", "+00:00")).astimezone(UTC)
    clock = (
        None if whole_day or not part.get("localTime") else time.fromisoformat(part["localTime"])
    )
    return local_start(date.fromisoformat(part["localDate"]), clock, tz)


def _prices(ranges: list[dict[str, Any]]) -> tuple[Decimal | None, Decimal | None, str | None]:
    try:
        mins = [Decimal(str(r["min"])) for r in ranges if r.get("min") is not None]
        maxes = [Decimal(str(r["max"])) for r in ranges if r.get("max") is not None]
    except InvalidOperation:
        return None, None, None
    if not mins and not maxes:
        return None, None, None
    return (min(mins) if mins else None, max(maxes) if maxes else None, ranges[0].get("currency"))


def _image(images: list[dict[str, Any]]) -> str | None:
    wide = [
        i for i in images if i.get("ratio") == "16_9" and (i.get("width") or 0) <= MAX_IMAGE_WIDTH
    ]
    return max(wide, key=lambda i: i.get("width") or 0)["url"] if wide else None


def _venue(raw: dict[str, Any]) -> Venue:
    location = raw.get("location") or {}
    lat, lon = location.get("latitude"), location.get("longitude")
    return Venue(
        source=SOURCE,
        source_id=raw["id"],
        name=raw.get("name") or "Unnamed venue",
        address=(raw.get("address") or {}).get("line1"),
        lat=float(lat) if lat is not None else None,
        lon=float(lon) if lon is not None else None,
    )


def _api_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
