"""Events and venues in the shape of the migration 001 tables, plus the time rules.

Sources parse their own payloads into these models. Anything a source can't map
becomes a Skipped with a reason, so the run can count it.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, field_validator, model_validator

Category = Literal[
    "Music", "Comedy", "Theatre", "Sport", "Nightlife", "Arts", "Family", "Food & Drink"
]

# Same rule as the events_public_read policy (001) and the retention job (0007):
# an event with no end time counts as ended three hours after it starts.
FALLBACK_DURATION = timedelta(hours=3)


class Venue(BaseModel):
    source: str
    source_id: str
    name: str
    address: str | None = None
    lat: float | None = None
    lon: float | None = None


class Event(BaseModel):
    source: str
    source_id: str
    title: str
    category: Category
    genre: str | None = None
    start_at: datetime
    end_at: datetime | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    currency: str | None = None
    url: str | None = None
    image_url: str | None = None
    lat: float | None = None
    lon: float | None = None
    status: Literal["active", "cancelled"] = "active"
    venue: Venue | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("datetimes must carry a timezone")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "Event":
        # The table's CHECK (price_min <= price_max) would reject the row, so drop
        # an inverted range rather than lose the event.
        if self.price_min is not None and self.price_max is not None:
            if self.price_min > self.price_max:
                self.price_min = self.price_max = None
        if self.end_at is not None and self.end_at < self.start_at:
            raise ValueError("end_at is before start_at")
        return self

    def effective_end(self) -> datetime:
        return self.end_at or self.start_at + FALLBACK_DURATION

    def has_ended(self, now: datetime) -> bool:
        return self.effective_end() <= now


@dataclass(frozen=True)
class Skipped:
    reason: str
    source_id: str | None = None
    delete: bool = False  # True when an existing row must also go (postponed events)


def local_start(day: date, clock: time | None, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, clock or time(0), tzinfo=tz).astimezone(UTC)


def end_of_local_day(day: date, tz: ZoneInfo) -> datetime:
    next_day = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
    return (next_day - timedelta(seconds=1)).astimezone(UTC)
