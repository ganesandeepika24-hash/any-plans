"""Skiddle events API: fetch London events by date window and offset, and map them.

Skiddle is approved for non-commercial use (decision 0001). It publishes no rate
limit, so requests are kept to one a second.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from jobs.geo import km_to_miles
from jobs.http import CapReached, Failure, RateLimitedClient, RequestFailed
from jobs.normalise import Category, Event, Skipped, Venue, end_of_local_day, local_start

SOURCE = "skiddle"
URL = "https://www.skiddle.com/api/v1/events/search/"
MIN_INTERVAL = 1.0
PER_RUN_CAP = 500
DAILY_STOP = 1000
REQUESTED_LIMIT = 100
WINDOW_DAYS = 7
# Offset 10,000 failed in dry run 40386725066 (totalcount 10,425). The search is
# Elasticsearch-backed, which stops at the 10,000th result by default.
RESULT_LIMIT = 10_000

EVENT_CODES: dict[str, Category] = {
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
# Reviewed against the dry-run sample (decision 0008): not dated nights out.
SKIPPED_CODES = frozenset(
    {"TOUR", "EDU", "EXPER", "NET", "HEALTH", "CONF", "RELIG", "HOBBY", "AWARD", "FUND", ""}
)
_NUMBER = re.compile(r"\d+(?:\.\d{1,2})?")
_ZERO = re.compile(r"0+(?:\.0+)?")


@dataclass(frozen=True)
class DateWindow:
    start: date  # minDate
    end: date  # maxDate, and the next window's minDate

    # Windows share their boundary day, so nothing is missed whether maxDate is
    # inclusive or exclusive. Events seen twice are dropped by source ID.

    def can_split(self) -> bool:
        return (self.end - self.start).days >= 2

    def split(self) -> tuple["DateWindow", "DateWindow"]:
        if not self.can_split():
            raise ValueError(f"window {self.label} can't be split")
        mid = self.start + timedelta(days=(self.end - self.start).days // 2)
        return DateWindow(self.start, mid), DateWindow(mid, self.end)

    @property
    def label(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


def date_windows(start: date, end: date) -> list[DateWindow]:
    windows = []
    while start < end:
        windows.append(DateWindow(start, min(start + timedelta(days=WINDOW_DAYS), end)))
        start += timedelta(days=WINDOW_DAYS)
    return windows


@dataclass
class FetchResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    failed_windows: list[Failure] = field(default_factory=list)
    windows: list[dict[str, Any]] = field(default_factory=list)  # totalcount, results, pages
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
    windows: list[DateWindow],
    on_page: Callable[[DateWindow, int, Any], None] | None = None,
) -> FetchResult:
    result = FetchResult()
    params = {
        "api_key": api_key,
        "latitude": f"{lat:.6f}",
        "longitude": f"{lon:.6f}",
        # Skiddle takes whole miles; round up and filter back to radius_km in code.
        "radius": int(km_to_miles(radius_km)) + 1,
        "limit": REQUESTED_LIMIT,
        "order": "date",
    }
    pending = list(windows)
    try:
        while pending:
            window = pending.pop(0)
            record: dict[str, Any] = {
                "window": window.label,
                "totalcount": None,
                "results": 0,
                "pages": [],
            }
            result.windows.append(record)
            try:
                _fetch_window(client, params, window, pending, result, record, on_page)
            except RequestFailed as error:
                result.failed_windows.append(
                    Failure(window.label, error.status, error.message, record.get("offset"))
                )
    except CapReached as cap:
        result.cap_reached = cap.reason
    return result


def _fetch_window(client, params, window, pending, result, record, on_page) -> None:
    offset = 0
    while True:
        record["offset"] = offset  # kept only if this request fails
        body = client.get_json(
            URL,
            {
                **params,
                "minDate": window.start.isoformat(),
                "maxDate": window.end.isoformat(),
                "offset": offset,
            },
        )
        if on_page:
            on_page(window, offset, body)
        if str(body.get("error", "0")) not in ("0", "False", "false"):
            raise RequestFailed(
                200,
                f"Skiddle error {body.get('error')}: "
                f"{body.get('errormessage') or body.get('message') or ''}",
            )
        total = int(body.get("totalcount") or 0)
        results = body.get("results") or []
        record["totalcount"] = total
        record["pages"].append(
            {"offset": offset, "results": len(results), "pagecount": body.get("pagecount")}
        )
        if body.get("pagecount") is not None:
            result.page_sizes.add(int(body["pagecount"]))

        if offset == 0 and total > RESULT_LIMIT:
            if window.can_split():
                pending[:0] = window.split()
                result.split_windows += 1
                record["split"] = True
                record.pop("offset")
                return
            result.failed_windows.append(
                Failure(window.label, None, f"{total} results in a window that can't be split")
            )

        result.items.extend(results)
        record["results"] += len(results)
        # Advance by what came back, not by the limit we asked for.
        offset += len(results)
        if not results or offset >= total or offset >= RESULT_LIMIT:
            record.pop("offset")
            return


def parse(raw: dict[str, Any], tz: ZoneInfo) -> Event | Skipped:
    source_id = str(raw.get("id")) if raw.get("id") is not None else None
    code = raw.get("EventCode") or ""
    if code in SKIPPED_CODES:
        return Skipped(f"skipped event code ({code or 'empty'})", source_id)
    category = EVENT_CODES.get(code)
    if category is None:
        return Skipped(f"unmapped event code ({code})", source_id)  # a code we haven't reviewed

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

    price_min, price_max = prices(raw)
    venue_raw = raw.get("venue") or None
    venue = _venue(venue_raw) if venue_raw else None

    return Event(
        source=SOURCE,
        source_id=source_id,
        title=raw["eventname"],
        category=category,
        genre=None,  # Skiddle's results have no genres field
        start_at=start_at,
        end_at=end_at,
        price_min=price_min,
        price_max=price_max,
        currency=(raw.get("currency") or None) if price_min is not None else None,
        url=raw.get("link"),
        image_url=raw.get("largeimageurl") or None,
        lat=venue.lat if venue else None,
        lon=venue.lon if venue else None,
        status="cancelled" if str(raw.get("cancelled", "0")) == "1" else "active",
        venue=venue,
    )


def prices(raw: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    """entryprice first, then ticketpricing (decision 0008).

    In the dry-run sample the two never agreed when both were set, and entryprice held
    the real range (Harry Potter: "£30.00 - £210.00" against ticketpricing 2.99).
    ticketpricing of 0/0 also appeared on paid shows, so it means unknown, not free.
    An entryprice of "0" or "0.00" is unknown too: it appeared on two paid festivals,
    whose ticketpricing values were placeholders. Only "Free" means free.
    """
    if _ZERO.fullmatch((raw.get("entryprice") or "").strip()):
        return None, None
    low, high = parse_price(raw.get("entryprice"))
    if low is not None:
        return low, high
    pricing = raw.get("ticketpricing") or {}
    low, high = pricing.get("minPrice"), pricing.get("maxPrice")
    if low is None or high is None or (low == 0 and high == 0):
        return None, None
    return Decimal(str(low)), Decimal(str(high))


def parse_price(text: str | None) -> tuple[Decimal | None, Decimal | None]:
    """Seen in the sample: "£54.00 - £66.00", "65", "£55.00", "GBP 5.00 - GBP 13.00",
    "General Admission: GBP 25.00", "5-40"."""
    if not text or not text.strip():
        return None, None
    if text.strip().lower() == "free":
        return Decimal(0), Decimal(0)
    amounts = [Decimal(a) for a in _NUMBER.findall(text)]
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
