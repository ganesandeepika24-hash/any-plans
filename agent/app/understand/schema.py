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
    # Spans (decision 0020): code works out the days, and a recurring day with no count
    # or month asks which ones.
    "month",
    "this_month",
    "next_month",
    "range",
    "season",
    "every_weekday",
    "every_weekend",
]
Season = Literal["winter", "spring", "summer", "autumn"]
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
    """The fields after holiday came with prompt v6 (decision 0020). They default to null,
    so replies from earlier prompts still validate."""

    kind: WhenKind
    weekday: Weekday | None  # kind weekday or every_weekday, or with a date
    qualifier: Literal["this", "next"] | None  # "this Thursday", "next Friday"
    month: int | None = Field(ge=1, le=12)  # a date's, a range's start, a month
    day: int | None = Field(ge=1, le=31)
    holiday: Holiday | None
    end_month: int | None = Field(default=None, ge=1, le=12)  # a range's last day
    end_day: int | None = Field(default=None, ge=1, le=31)
    season: Season | None = None
    count: int | None = Field(default=None, ge=1, le=12)  # "the next 4 Saturdays"
    within: Literal["this_month", "next_month"] | None = None  # "Saturdays this month"

    @model_validator(mode="after")
    def _parts_match_kind(self) -> Self:
        every = self.kind in ("every_weekday", "every_weekend")
        if self.kind in ("weekday", "every_weekday") and self.weekday is None:
            raise ValueError(f"kind '{self.kind}' needs a weekday")
        if self.weekday is not None and self.kind not in ("weekday", "date", "every_weekday"):
            raise ValueError("weekday goes only with kind 'weekday', 'date' or 'every_weekday'")
        if self.qualifier is not None and (self.weekday is None or self.kind == "every_weekday"):
            raise ValueError("qualifier goes only with a weekday or a date's weekday")
        needs_day = self.kind in ("date", "range")
        if (self.day is not None) != needs_day:
            raise ValueError("day is set exactly when kind is 'date' or 'range'")
        if needs_day or self.kind == "month":
            if self.month is None:
                raise ValueError(f"kind '{self.kind}' needs a month")
        elif self.month is not None and not every:
            raise ValueError("month goes only with a date, a range, a month or a recurring day")
        if ((self.end_month, self.end_day) != (None, None)) != (self.kind == "range"):
            raise ValueError("end_month and end_day are set exactly when kind is 'range'")
        if self.kind == "range" and None in (self.end_month, self.end_day):
            raise ValueError("a range needs both end_month and end_day")
        if (self.holiday is not None) != (self.kind == "holiday"):
            raise ValueError("holiday is set exactly when kind is 'holiday'")
        if (self.season is not None) != (self.kind == "season"):
            raise ValueError("season is set exactly when kind is 'season'")
        if (self.count is not None or self.within is not None) and not every:
            raise ValueError("count and within go only with a recurring day")
        if every and sum(v is not None for v in (self.count, self.within, self.month)) > 1:
            raise ValueError("a recurring day takes one of count, within and month")
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


AGENT_ONLY_FILTERS = ("windows",)


class SearchFilters(BaseModel):
    """search_items' filters, key for key (migration 010; a test compares them), plus
    AGENT_ONLY_FILTERS, which the Search node applies itself."""

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
    # The agent's own key, not search_items': the days inside date_from to date_to that
    # count ("the next 4 Saturdays", decision 0020). The Search node keeps only events
    # on during one of them.
    windows: list[tuple[AwareDatetime, AwareDatetime]] | None = Field(default=None, min_length=2)

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
        if self.windows is not None:
            if self.date_from is None or self.date_to is None:
                raise ValueError("windows need date_from and date_to")
            bounds = [t for w in self.windows for t in w]
            if bounds != sorted(bounds) or any(a >= b for a, b in self.windows):
                raise ValueError("windows must be in order and not overlap")
            if bounds[0] < self.date_from or bounds[-1] > self.date_to:
                raise ValueError("windows must fall inside date_from to date_to")
        place = (self.lat, self.lon, self.radius_km)
        if any(v is not None for v in place) and any(v is None for v in place):
            raise ValueError("lat, lon and radius_km go together")
        return self

    def to_json(self) -> dict[str, Any]:
        """The filters as JSON: only the keys that are set. search_items takes all but
        `windows`, which the Search node takes off first."""
        out = self.model_dump(exclude_none=True)
        for key in ("date_from", "date_to"):
            if key in out:
                out[key] = _iso(out[key])
        if "windows" in out:
            out["windows"] = [[_iso(a), _iso(b)] for a, b in out["windows"]]
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
