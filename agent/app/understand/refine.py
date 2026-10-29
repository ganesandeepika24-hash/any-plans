"""Refine: an earlier search, changed by a follow-up (stage 1 step 2, decision 0018).

The model reads only the follow-up and says what changes (a RefineReply). Code does the
rest (principle 5):

- Explicit values ("on Saturday instead", "in Camden", "under £15") are resolved as a
  search would be, and replace the earlier ones of the same kind.
- Relative changes use the steps in config (refine_steps): "cheaper" multiplies the price
  limit, "closer" the radius, and the opposite changes divide by the same step.
- Everything the follow-up doesn't mention is kept from the earlier search's stored
  filters, its search text, vibe and who.

The earlier filters are stored without the user's own point (0013). If the earlier search
used it, the follow-up must send the location again, or it asks where.

The default-area note follows the refined search, not the follow-up alone: "make it
cheaper" on a Soho search shows no "Showing central London.", and on a search of the
default area it still does (step 1.4 fix 3). So does the per-ticket cap note.

Party size (decision 0024): the earlier search's stated budget is stored beside its
filters. "It's 6 of us now" divides a total again, and leaves a per-person cap alone; a
budget that never said which asks, as a search would. "Cheaper" and "pricier" change the
per-ticket cap, which is then the stated budget, per person.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.understand.resolve import (
    KM_PER_MILE,
    LOCATION_RADIUS_KM,
    MAX_RADIUS_KM,
    MISSING_ORDER,
    NO_RANGES,
    Budget,
    City,
    DateRanges,
    Missing,
    Note,
    Point,
    Resolution,
    default_area_note,
    price_fields,
    resolve,
    ticket_cap_note,
)
from app.understand.schema import RefineReply, SearchFilters, UnderstoodQuery

STEPS_KEY = "refine_steps"
_DATE_KEYS = ("date_from", "date_to", "windows")
_PLACE_KEYS = ("lat", "lon", "radius_km")
_PRICE_KEYS = ("price_min", "price_max", "include_unpriced")


class RefineSteps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cheaper: float = Field(gt=0, lt=1)  # price limit × this; "pricier" divides by it
    closer: float = Field(gt=0, lt=1)  # radius × this; "further" divides by it
    min_radius_km: float = Field(gt=0, le=MAX_RADIUS_KM)


@dataclass(frozen=True)
class Prior:
    """The earlier search, as its queries row stored it."""

    filters: dict[str, Any]  # search_items keys, without the user's own point
    embedding_text: str | None
    vibe: str | None = None
    who: str | None = None
    budget: dict[str, Any] | None = None  # the stated Budget, as queries.budget stores it

    @property
    def used_location(self) -> bool:
        """A radius with no centre: the centre was the user's own point, not stored."""
        return "radius_km" in self.filters and "lat" not in self.filters


@dataclass(frozen=True)
class Refinement:
    resolution: Resolution
    vibe: str | None
    who: str | None


def apply(
    prior: Prior,
    reply: RefineReply,
    now: datetime,
    city: City,
    steps: RefineSteps,
    location: Point | None = None,
    ranges: DateRanges = NO_RANGES,
) -> Refinement:
    said = UnderstoodQuery.model_validate(
        reply.model_dump(exclude={"price_change", "distance_change", "distance"})
        | {"distance": None}
    )
    # What the follow-up says on its own, resolved as a search would be.
    fresh = resolve(said, now, city, location, ranges)
    new = fresh.filters.model_dump(exclude_none=True)
    f = dict(prior.filters)
    # The follow-up alone always reads as the default area; whether the refined search is
    # the default is decided at the end.
    # The price notes are worked out again below, with the earlier party size and budget.
    notes: list[Note] = [
        n
        for n in fresh.notes
        if n.code not in ("default_area", "ticket_cap", "price_range_inverted")
    ]
    missing: set[Missing] = set()
    used_location, source = False, None

    if reply.when is not None or reply.event is not None or "date" in reply.unclear:
        _take(f, new, _DATE_KEYS)
        missing |= {m for m in fresh.missing if m in ("date", "event_date")}

    if reply.categories:
        f["categories"] = new["categories"]

    if reply.place is not None or reply.near_me:
        _take(f, new, _PLACE_KEYS)
        used_location, source = fresh.used_location, fresh.location_source
        missing |= {m for m in fresh.missing if m == "location"}
    elif prior.used_location:
        # The earlier centre was the user's point; it has to come with the follow-up.
        if location is not None:
            f["lat"], f["lon"] = location.lat, location.lon
            used_location, source = True, location.source
        else:
            missing.add("location")

    budget = Budget.from_json(prior.budget)
    if reply.party_size is not None:
        f["party_size"] = reply.party_size
    restated = reply.price_min is not None or reply.price_max is not None
    if reply.free or ("budget" in reply.unclear and not restated):
        _take(f, new, _PRICE_KEYS)
        missing |= {m for m in fresh.missing if m == "budget"}
        budget = None
    elif restated or reply.price_scope is not None or reply.party_size is not None:
        if restated:
            budget = Budget(reply.price_min, reply.price_max, reply.price_scope)
        elif budget is not None and reply.price_scope is not None:
            budget = replace(budget, basis=reply.price_scope)  # "that's per head"
        if budget is not None:
            # A budget given before 026 has no stored budget: its per-ticket cap is kept.
            for key in _PRICE_KEYS:
                f.pop(key, None)
            price_fields(budget, f.get("party_size"), f, notes, missing)

    if reply.distance is not None:
        d = reply.distance
        f["radius_km"] = d.value * (KM_PER_MILE if d.unit == "mi" else 1.0)
        if f["radius_km"] > MAX_RADIUS_KM:
            notes.append(
                Note("radius_capped", f"Searching within {MAX_RADIUS_KM:g} km, the furthest.")
            )
            f["radius_km"] = MAX_RADIUS_KM
        if "lat" not in f and "location" not in missing:
            if location is None:
                missing.add("location")  # a distance from where?
            else:
                f["lat"], f["lon"] = location.lat, location.lon
                used_location, source = True, location.source

    cap = f.get("price_max")
    _change_price(f, reply, steps, notes, missing)
    if f.get("price_max") != cap:
        budget = Budget(f.get("price_min"), f["price_max"], "each")
    used_location, source = _change_distance(
        f, reply, steps, notes, missing, location, used_location, source
    )

    if missing & {"location"} or ("radius_km" in f and "lat" not in f):
        for key in _PLACE_KEYS:  # no centre: search_items needs all three or none
            f.pop(key, None)
        used_location, source = False, None

    if _is_default_area(f, city) and not used_location:
        notes.append(default_area_note(city))
    if (note := ticket_cap_note(f)) is not None:
        notes.append(note)

    resolution = Resolution(
        SearchFilters(**f),
        reply.keywords or prior.embedding_text,
        tuple(notes),
        tuple(m for m in MISSING_ORDER if m in missing),
        used_location,
        source,
        fresh.date_choices if "date" in missing else (),
        budget,
    )
    return Refinement(resolution, reply.vibe or prior.vibe, reply.who or prior.who)


def _take(f: dict, new: dict, keys: tuple[str, ...]) -> None:
    for key in keys:
        f.pop(key, None)
        if key in new:
            f[key] = new[key]


def _is_default_area(f: dict[str, Any], city: City) -> bool:
    """The refined search is centred on the city's centre: the default area, kept from
    the earlier search or given again. A changed radius is still the default area."""
    centre = city.centre
    return centre is not None and (f.get("lat"), f.get("lon")) == (centre.lat, centre.lon)


def _change_price(f, reply, steps: RefineSteps, notes, missing) -> None:
    high = f.get("price_max")
    match reply.price_change:
        case "cheaper":
            if high is None:
                missing.add("budget")  # cheaper than what? Ask for a budget.
            elif high == 0:
                notes.append(Note("already_free", "These are already free."))
            else:
                f["price_max"] = round(high * steps.cheaper)
                if f.get("price_min") is not None and f["price_min"] > f["price_max"]:
                    del f["price_min"]
        case "pricier":
            if high is None or high == 0:
                notes.append(Note("no_price_limit", "There was no price limit to raise."))
            else:
                f["price_max"] = round(high / steps.cheaper)


def _change_distance(f, reply, steps: RefineSteps, notes, missing, location, used, source):
    if reply.distance_change is None:
        return used, source
    radius = f.get("radius_km")
    if radius is None:
        # Nothing to be closer to yet: use the user's point if it came, or ask.
        if location is None:
            missing.add("location")
            return used, source
        f["lat"], f["lon"] = location.lat, location.lon
        radius, used, source = LOCATION_RADIUS_KM, True, location.source
    if reply.distance_change == "closer":
        smaller = radius * steps.closer
        if smaller < steps.min_radius_km:
            notes.append(
                Note("radius_floor", f"Searching within {steps.min_radius_km:g} km, the closest.")
            )
            smaller = steps.min_radius_km
        f["radius_km"] = smaller
    else:
        f["radius_km"] = min(MAX_RADIUS_KM, radius / steps.closer)
    return used, source
