"""Turn an UnderstoodQuery into SearchFilters (principle 5: code computes, decision 0012).

Days run from 05:00 to 05:00 local time, so a late show counts as the evening before.
Evenings start at 17:00. A weekend runs from Friday 17:00 to Monday 05:00. Every window
starts no earlier than now. A place or date that can't be resolved drops that filter
and adds a note, so the UI can say what it ignored.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.understand.schema import SearchFilters, UnderstoodQuery, When

DAY_START = time(5)
EVENING_START = time(17)
MAX_RADIUS_KM = 100.0  # search_items' limit
KM_PER_MILE = 1.609344
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
FRIDAY = 4

NoteCode = Literal[
    "unknown_place",
    "no_such_date",
    "distance_without_place",
    "radius_capped",
    "price_range_inverted",
]


@dataclass(frozen=True)
class Area:
    id: str
    name: str
    aliases: tuple[str, ...]
    lat: float
    lon: float
    default_radius_km: float


@dataclass(frozen=True)
class City:
    id: str
    name: str
    tz: ZoneInfo
    areas: tuple[Area, ...]

    def find_area(self, place: str) -> Area | None:
        wanted = normalise(place)
        if not wanted:
            return None
        for area in self.areas:
            if wanted in {normalise(n) for n in (area.name, *area.aliases)}:
                return area
        return None


@dataclass(frozen=True)
class Note:
    code: NoteCode
    message: str


@dataclass(frozen=True)
class Resolution:
    filters: SearchFilters
    query_text: str | None  # what Retrieve embeds; None for a filter-only search
    notes: tuple[Note, ...]


def normalise(name: str) -> str:
    text = name.casefold().replace("&", " and ")
    text = re.sub(r"[’'.]", "", text)
    text = " ".join(re.sub(r"[^\w]+", " ", text).split())
    return text.removeprefix("the ")


def resolve(understood: UnderstoodQuery, now: datetime, city: City) -> Resolution:
    notes: list[Note] = []
    fields: dict = {}

    if understood.when is not None:
        window = date_window(understood.when, now, city.tz)
        if window is None:
            notes.append(Note("no_such_date", _no_such_date(understood.when)))
        else:
            fields["date_from"], fields["date_to"] = window

    if understood.categories:
        fields["categories"] = understood.categories

    _resolve_place(understood, city, fields, notes)
    _resolve_price(understood, fields, notes)

    return Resolution(SearchFilters(**fields), understood.keywords, tuple(notes))


# Dates ---------------------------------------------------------------------------


def date_window(when: When, now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    local_now = now.astimezone(tz)
    # The "day" before 05:00 is still yesterday's.
    day = local_now.date() - timedelta(days=1 if local_now.time() < DAY_START else 0)

    def at(d: date, t: time = DAY_START) -> datetime:
        return datetime.combine(d, t, tzinfo=tz)

    match when.kind:
        case "today":
            start, end = local_now, at(day + timedelta(days=1))
        case "tonight":
            start, end = at(day, EVENING_START), at(day + timedelta(days=1))
        case "tomorrow":
            start, end = at(day + timedelta(days=1)), at(day + timedelta(days=2))
        case "this_weekend" | "next_weekend":
            friday = _weekend_friday(day) + timedelta(days=7 if when.kind == "next_weekend" else 0)
            start, end = at(friday, EVENING_START), at(friday + timedelta(days=3))
        case "this_week":
            monday = day + timedelta(days=7 - day.weekday())
            start, end = local_now, at(monday)
        case "next_week":
            monday = day + timedelta(days=7 - day.weekday())
            start, end = at(monday), at(monday + timedelta(days=7))
        case "weekday":
            target = day + timedelta(days=(WEEKDAYS.index(when.weekday) - day.weekday()) % 7)
            start, end = at(target), at(target + timedelta(days=1))
        case "date":
            target = _next_date(when.month, when.day, day)
            if target is None:
                return None
            start, end = at(target), at(target + timedelta(days=1))
    start = max(start, local_now)
    return (start, end) if start < end else None


def _weekend_friday(day: date) -> date:
    """The Friday of the weekend that contains `day`, or of the next one."""
    if day.weekday() >= FRIDAY:
        return day - timedelta(days=day.weekday() - FRIDAY)
    return day + timedelta(days=FRIDAY - day.weekday())


def _next_date(month: int, day_of_month: int, today: date) -> date | None:
    """The next `day_of_month` of `month` on or after today, within a year."""
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day_of_month)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


def _no_such_date(when: When) -> str:
    if when.kind == "date":
        return f"No {when.day}/{when.month} in the coming year, so dates were ignored."
    return "That time has already passed, so dates were ignored."


# Place and price -----------------------------------------------------------------


def _resolve_place(understood: UnderstoodQuery, city: City, fields: dict, notes: list) -> None:
    place = understood.place
    if place is not None and normalise(place) == normalise(city.name):
        place = None  # the whole city: no location filter

    area = city.find_area(place) if place is not None else None
    if place is not None and area is None:
        notes.append(
            Note("unknown_place", f"Couldn't find '{place}', so showing all of {city.name}.")
        )

    if understood.distance is not None and area is None:
        if place is None:
            notes.append(
                Note("distance_without_place", "A distance needs a place, so it was ignored.")
            )
        return
    if area is None:
        return

    radius = area.default_radius_km
    if understood.distance is not None:
        d = understood.distance
        radius = d.value * (KM_PER_MILE if d.unit == "mi" else 1.0)
        if radius > MAX_RADIUS_KM:
            notes.append(
                Note("radius_capped", f"Searching within {MAX_RADIUS_KM:g} km, the furthest.")
            )
            radius = MAX_RADIUS_KM
    fields["lat"], fields["lon"], fields["radius_km"] = area.lat, area.lon, radius


def _resolve_price(understood: UnderstoodQuery, fields: dict, notes: list) -> None:
    if understood.free:
        # Free means a known price of 0, so unpriced events don't count.
        fields["price_max"], fields["include_unpriced"] = 0.0, False
        return
    low, high = understood.price_min, understood.price_max
    if low is not None and high is not None and low > high:
        notes.append(
            Note(
                "price_range_inverted",
                "The lowest price was above the highest, so prices were ignored.",
            )
        )
        return
    if low is not None:
        fields["price_min"] = low
    if high is not None:
        fields["price_max"] = high
