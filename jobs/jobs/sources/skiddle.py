"""Skiddle events API: fetch London events by offset paging and map them.

Skiddle is approved for non-commercial use (decision 0001). It publishes no rate
limit, so requests are kept to one a second.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from jobs.geo import km_to_miles
from jobs.http import CapReached, RateLimitedClient, RequestFailed
from jobs.normalise import Category, Event, Skipped, Venue, end_of_local_day, local_start

SOURCE = "skiddle"
URL = "https://www.skiddle.com/api/v1/events/search/"
MIN_INTERVAL = 1.0
PER_RUN_CAP = 500
DAILY_STOP = 1000
REQUESTED_LIMIT = 100

EVENT_CODES: dict[str, Category] = {
    "LIVE": "Music",
    "FEST": "Music",
    "CLUB": "Nightlife",
    "BARPUB": "Nightlife",
    "COMEDY": "Comedy",
    "THEATRE": "Theatre",
    "EXHIB": "Arts",
    "KIDS": "Family",
    "SPORT": "Sport",
}
_AMOUNT = re.compile(r"£\s*(\d+(?:\.\d{1,2})?)")


@dataclass
class FetchResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    failed: bool = False
    cap_reached: str | None = None

    @property
    def complete(self) -> bool:
        return not self.failed and self.cap_reached is None


def fetch(
    client: RateLimitedClient,
    api_key: str,
    lat: float,
    lon: float,
    radius_km: float,
    start: date,
    end: date,
    on_page: Callable[[int, Any], None] | None = None,
) -> FetchResult:
    result = FetchResult()
    params = {
        "api_key": api_key,
        "latitude": f"{lat:.6f}",
        "longitude": f"{lon:.6f}",
        # Skiddle takes whole miles; round up and filter back to radius_km in code.
        "radius": int(km_to_miles(radius_km)) + 1,
        "minDate": start.isoformat(),
        "maxDate": end.isoformat(),
        "limit": REQUESTED_LIMIT,
        "order": "date",
        "offset": 0,
    }
    offset = 0
    try:
        while True:
            body = client.get_json(URL, {**params, "offset": offset})
            if on_page:
                on_page(offset, body)
            if str(body.get("error", "0")) not in ("0", "False", "false"):
                raise RequestFailed(f"Skiddle error {body.get('error')}")
            results = body.get("results") or []
            result.items.extend(results)
            # Advance by what came back, not by the limit we asked for.
            offset += len(results)
            if not results or offset >= int(body.get("totalcount") or 0):
                break
    except RequestFailed:
        result.failed = True
    except CapReached as cap:
        result.cap_reached = cap.reason
    return result


def parse(raw: dict[str, Any], tz: ZoneInfo) -> Event | Skipped:
    source_id = str(raw.get("id")) if raw.get("id") is not None else None
    category = EVENT_CODES.get(raw.get("EventCode") or "")
    if category is None:
        return Skipped(f"unmapped event code ({raw.get('EventCode')})", source_id)

    start_at = _timestamp(raw.get("startdate"), tz)
    whole_day = start_at is None and bool(raw.get("date"))
    if whole_day:
        start_at = local_start(date.fromisoformat(raw["date"]), None, tz)
    if start_at is None:
        return Skipped("no date", source_id)
    end_at = _timestamp(raw.get("enddate"), tz)
    if end_at is not None and end_at <= start_at:
        end_at = None
    if end_at is None and whole_day:
        end_at = end_of_local_day(date.fromisoformat(raw["date"]), tz)

    price_min, price_max = parse_price(raw.get("entryprice"))
    genres = raw.get("genres") or []
    venue_raw = raw.get("venue") or None
    venue = _venue(venue_raw) if venue_raw else None

    return Event(
        source=SOURCE,
        source_id=source_id,
        title=raw["eventname"],
        category=category,
        genre=genres[0]["name"].lower() if genres and genres[0].get("name") else None,
        start_at=start_at,
        end_at=end_at,
        price_min=price_min,
        price_max=price_max,
        currency="GBP" if price_min is not None else None,
        url=raw.get("link"),
        image_url=raw.get("largeimageurl") or None,
        lat=venue.lat if venue else None,
        lon=venue.lon if venue else None,
        status="cancelled" if str(raw.get("cancelled", "0")) == "1" else "active",
        venue=venue,
    )


def parse_price(text: str | None) -> tuple[Decimal | None, Decimal | None]:
    if not text or not text.strip():
        return None, None
    if text.strip().lower() == "free":
        return Decimal(0), Decimal(0)
    amounts = [Decimal(a) for a in _AMOUNT.findall(text)]
    if not amounts:
        return None, None
    return min(amounts), max(amounts)


def _timestamp(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)  # Skiddle times without an offset are local
    return parsed.astimezone(UTC)


def _venue(raw: dict[str, Any]) -> Venue:
    lat, lon = raw.get("latitude"), raw.get("longitude")
    address = ", ".join(p for p in (raw.get("address"), raw.get("town"), raw.get("postcode")) if p)
    return Venue(
        source=SOURCE,
        source_id=str(raw["id"]),
        name=raw.get("name") or "Unnamed venue",
        address=address or None,
        lat=float(lat) if lat not in (None, "") else None,
        lon=float(lon) if lon not in (None, "") else None,
    )
