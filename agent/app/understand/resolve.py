"""Turn an UnderstoodQuery into SearchFilters (principle 5: code computes, decision 0012).

Days run from 05:00 to 05:00 local time, so a late show counts as the evening before.
Evenings start at 17:00. A weekend runs from Friday 17:00 to Monday 05:00. Every window
starts no earlier than now. A place or date that can't be resolved drops that filter
and adds a note, so the UI can say what it ignored.

When the search can't run without asking (a vague or conflicting date, no location
for "near me", "cheap", a total price, an event with no date, no idea what kind of
thing), the filter is left out and the reason goes in `missing`. The rest still
resolves, so the answer only fills the gap.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.understand.holidays import holiday_days
from app.understand.schema import SearchFilters, UnderstoodQuery, When

DAY_START = time(5)
EVENING_START = time(17)
MAX_RADIUS_KM = 100.0  # search_items' limit; the UI slider's 6 miles is the UI's cap
KM_PER_MILE = 1.609344
LOCATION_RADIUS_KM = KM_PER_MILE  # the slider's default, 1 mile, around the user's point
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
FRIDAY = 4

NoteCode = Literal[
    "unknown_place",
    "no_such_date",
    "radius_capped",
    "price_range_inverted",
]
# The complete list, in the order the app asks.
Missing = Literal["location", "date", "budget", "price_basis", "event_date", "category"]
MISSING_ORDER: tuple[Missing, ...] = (
    "location",
    "date",
    "budget",
    "price_basis",
    "event_date",
    "category",
)


class AmbiguousDate(Exception):
    """The date could mean more than one day, or its parts disagree."""


LocationSource = Literal["device", "pin"]


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float
    # For the user's own point: "device" (read once from the browser, with permission, for
    # "near me") or "pin" (dropped on the map). None for area centres, or when the app
    # doesn't say (decision 0017).
    source: LocationSource | None = None


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
    missing: tuple[Missing, ...] = ()
    used_location: bool = False  # lat and lon are the user's own point: never stored
    location_source: LocationSource | None = None  # that point's source, when used


def normalise(name: str) -> str:
    text = name.casefold().replace("&", " and ")
    text = re.sub(r"[’'.]", "", text)
    text = " ".join(re.sub(r"[^\w]+", " ", text).split())
    return text.removeprefix("the ")


def resolve(
    understood: UnderstoodQuery, now: datetime, city: City, location: Point | None = None
) -> Resolution:
    notes: list[Note] = []
    missing: set[Missing] = set()
    fields: dict = {}

    if "date" in understood.unclear:
        missing.add("date")
    elif understood.when is not None:
        try:
            window = date_window(understood.when, now, city.tz)
        except AmbiguousDate:
            missing.add("date")
        else:
            if window is None:
                notes.append(Note("no_such_date", _no_such_date(understood.when)))
            else:
                fields["date_from"], fields["date_to"] = window
    elif understood.event is not None:
        missing.add("event_date")

    if understood.categories:
        fields["categories"] = understood.categories
    elif "category" in understood.unclear and understood.vibe is None and understood.who is None:
        # A vibe or a group is enough to search on: "something chilled" asks nothing.
        missing.add("category")

    used_location = _resolve_place(understood, city, location, fields, notes, missing)
    _resolve_price(understood, fields, notes, missing)

    return Resolution(
        SearchFilters(**fields),
        understood.keywords,
        tuple(notes),
        tuple(m for m in MISSING_ORDER if m in missing),
        used_location,
        location.source if used_location and location is not None else None,
    )


# Dates ---------------------------------------------------------------------------


def date_window(when: When, now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    """The window `when` means, or None if it has passed or doesn't exist. Raises
    AmbiguousDate when it could mean more than one day."""
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
            candidates = _weekday_candidates(when, day)
            if len(candidates) > 1:
                raise AmbiguousDate
            target = candidates[0]
            start, end = at(target), at(target + timedelta(days=1))
        case "date":
            target = _next_date(when.month, when.day, day)
            if target is None:
                return None
            if when.weekday is not None:
                _check_weekday(when, target, day)
            start, end = at(target), at(target + timedelta(days=1))
        case "holiday":
            days = holiday_days(when.holiday, day)
            if days is None:
                return None
            first, last = days
            # A long weekend starts the evening before, like any weekend.
            if (last - first).days >= 2:
                start = at(first - timedelta(days=1), EVENING_START)
            else:
                start = at(first)
            end = at(last + timedelta(days=1))
    start = max(start, local_now)
    return (start, end) if start < end else None


def _weekday_candidates(when: When, today: date) -> list[date]:
    """The days a named weekday could mean. A bare or "this" weekday is the next one,
    today included. "Next Friday" on a Friday is a week today; on any other day it
    could be the coming Friday or the one after, so it's ambiguous."""
    coming = today + timedelta(days=(WEEKDAYS.index(when.weekday) - today.weekday()) % 7)
    if when.qualifier != "next":
        return [coming]
    if coming == today:
        return [coming + timedelta(days=7)]
    return [coming, coming + timedelta(days=7)]


def _check_weekday(when: When, target: date, today: date) -> None:
    """A date with a weekday resolves only when they agree ("Friday 6 November",
    "5th Nov next Thursday"); otherwise it's ambiguous ("Bonfire Night this Thursday")."""
    if when.qualifier is None:
        agrees = WEEKDAYS[target.weekday()] == when.weekday
    else:
        agrees = target in _weekday_candidates(when, today)
    if not agrees:
        raise AmbiguousDate


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
    if when.kind == "holiday":
        return "That holiday is beyond the calendar we have, so dates were ignored."
    return "That time has already passed, so dates were ignored."


# Place and price -----------------------------------------------------------------


def _resolve_place(
    understood: UnderstoodQuery,
    city: City,
    location: Point | None,
    fields: dict,
    notes: list,
    missing: set,
) -> bool:
    """Set the point and radius. A named area wins; then the user's own point (a pin or
    their location), sent with the request. Returns whether the user's point was used."""
    place = understood.place
    if place is not None and normalise(place) == normalise(city.name):
        place = None  # the whole city: no location filter

    area = city.find_area(place) if place is not None else None
    if place is not None and area is None:
        notes.append(
            Note("unknown_place", f"Couldn't find '{place}', so showing all of {city.name}.")
        )

    if area is not None:
        centre, radius = Point(area.lat, area.lon), area.default_radius_km
    elif location is not None:
        centre, radius = location, LOCATION_RADIUS_KM
    else:
        # "Near me" or a bare distance needs a point; don't guess one.
        if place is None and (understood.near_me or understood.distance is not None):
            missing.add("location")
        return False

    if understood.distance is not None:
        d = understood.distance
        radius = d.value * (KM_PER_MILE if d.unit == "mi" else 1.0)
        if radius > MAX_RADIUS_KM:
            notes.append(
                Note("radius_capped", f"Searching within {MAX_RADIUS_KM:g} km, the furthest.")
            )
            radius = MAX_RADIUS_KM
    fields["lat"], fields["lon"], fields["radius_km"] = centre.lat, centre.lon, radius
    return area is None


def _resolve_price(understood: UnderstoodQuery, fields: dict, notes: list, missing: set) -> None:
    if understood.free:
        # Free means a known price of 0, so unpriced events don't count.
        fields["price_max"], fields["include_unpriced"] = 0.0, False
        return
    low, high = understood.price_min, understood.price_max
    if "budget" in understood.unclear and low is None and high is None:
        missing.add("budget")  # "cheap": ask rather than pick a number
        return
    if understood.price_scope == "total" and (low is not None or high is not None):
        missing.add("price_basis")  # a total for how many? Prices are per ticket.
        return
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
