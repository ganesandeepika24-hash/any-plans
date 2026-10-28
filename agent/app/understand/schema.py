"""The two shapes in the Understand node (decision 0012).

UnderstoodQuery is what the model may return: intent in the user's words, no computed
dates, coordinates or conversions. SearchFilters is exactly what search_items accepts
(migration 010); only code builds it, from an UnderstoodQuery.
"""

import copy
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.understand.holidays import Holiday

Category = Literal[
    "Music", "Comedy", "Theatre", "Sport", "Nightlife", "Arts", "Family", "Food & Drink"
]
WhenKind = Literal[
    "today",
    "tonight",
    "tomorrow",
    "this_weekend",
    "next_weekend",
    "this_week",
    "next_week",
    "weekday",
    "date",
    "holiday",
]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# The PRD's vibe and who chips (round 2, decision 0015). They describe the outing, not
# the kind of event, so a search with one of them needs no category question.
Vibe = Literal["Chilled", "Lively", "Cultural", "Foodie", "Outdoors"]
Who = Literal["Solo", "Date", "Friends", "Family with kids"]
# What the model may say was too vague to search on. Code adds the other clarify
# reasons (location, price_basis, event_date) from the fields below.
Unclear = Literal["date", "budget", "category"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class When(_Strict):
    kind: WhenKind
    weekday: Weekday | None  # with kind weekday, or alongside a date ("Friday 6 November")
    qualifier: Literal["this", "next"] | None  # "this Thursday", "next Friday"
    month: int | None = Field(ge=1, le=12)
    day: int | None = Field(ge=1, le=31)
    holiday: Holiday | None

    @model_validator(mode="after")
    def _parts_match_kind(self) -> Self:
        if self.kind == "weekday" and self.weekday is None:
            raise ValueError("kind 'weekday' needs a weekday")
        if self.weekday is not None and self.kind not in ("weekday", "date"):
            raise ValueError("weekday goes only with kind 'weekday' or 'date'")
        if self.qualifier is not None and self.weekday is None:
            raise ValueError("qualifier goes only with a weekday")
        if ((self.month, self.day) != (None, None)) != (self.kind == "date"):
            raise ValueError("month and day are set exactly when kind is 'date'")
        if self.kind == "date" and None in (self.month, self.day):
            raise ValueError("a date needs both month and day")
        if (self.holiday is not None) != (self.kind == "holiday"):
            raise ValueError("holiday is set exactly when kind is 'holiday'")
        return self


class Distance(_Strict):
    value: float = Field(gt=0)
    unit: Literal["km", "mi"]


class UnderstoodQuery(_Strict):
    """Every field is required, with null for "not said", as Groq's strict mode needs."""

    keywords: str | None = Field(max_length=200)
    categories: list[Category] | None
    when: When | None
    place: str | None = Field(max_length=100)
    distance: Distance | None
    price_min: float | None = Field(ge=0)
    price_max: float | None = Field(ge=0)
    price_scope: Literal["each", "total"] | None  # "£60 each", "£60 total"
    free: bool
    near_me: bool  # "near me", "around here": needs the user's location
    event: str | None = Field(max_length=100)  # a named event whose date the user didn't give
    vibe: Vibe | None  # "chilled", "lively"
    who: Who | None  # "with the kids", "hen do"
    unclear: list[Unclear]

    @field_validator("keywords", "place", "event")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("categories")
    @classmethod
    def _empty_is_none(cls, value: list[str] | None) -> list[str] | None:
        return value or None

    @field_validator("unclear")
    @classmethod
    def _no_repeats(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


PriceChange = Literal["cheaper", "pricier"]
DistanceChange = Literal["closer", "further"]


class RefineReply(UnderstoodQuery):
    """A follow-up to an earlier search (decision 0018): what to change, in the same form
    as a search. Fields left null (false for free and near_me) keep the earlier search's
    values. The two changes are relative: code works out the new price or radius."""

    price_change: PriceChange | None  # "make it cheaper", "can spend more"
    distance_change: DistanceChange | None  # "somewhere closer", "further out is fine"


class SearchFilters(BaseModel):
    """search_items' filters, key for key (migration 010; a test compares them)."""

    model_config = ConfigDict(extra="forbid")

    date_from: AwareDatetime | None = None
    date_to: AwareDatetime | None = None
    categories: list[Category] | None = Field(default=None, min_length=1)
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    include_unpriced: bool | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(default=None, gt=0, le=100)
    limit: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.date_from and self.date_to and self.date_from >= self.date_to:
            raise ValueError("date_from must be before date_to")
        if (
            self.price_min is not None
            and self.price_max is not None
            and self.price_min > self.price_max
        ):
            raise ValueError("price_min must not exceed price_max")
        place = (self.lat, self.lon, self.radius_km)
        if any(v is not None for v in place) and any(v is None for v in place):
            raise ValueError("lat, lon and radius_km go together")
        return self

    def to_json(self) -> dict[str, Any]:
        """The jsonb argument for search_items: only the keys that are set."""
        out = self.model_dump(exclude_none=True)
        for key in ("date_from", "date_to"):
            if key in out:
                out[key] = _iso(out[key])
        return out


def _iso(moment: datetime) -> str:
    return moment.isoformat()  # always carries an offset: the fields are aware


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """`model`'s JSON schema in the form Groq's strict mode accepts: references inlined,
    every property required, additionalProperties false, no titles or defaults."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def fix(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return fix(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            node = {k: fix(v) for k, v in node.items() if k not in ("title", "default")}
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            return node
        if isinstance(node, list):
            return [fix(v) for v in node]
        return node

    return fix(schema)
