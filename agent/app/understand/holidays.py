"""Bank holidays for date phrases like "Easter Monday" (decision 0012).

The dates come from a snapshot of gov.uk's England and Wales list (bank_holidays.json,
refreshed with scripts/refresh_bank_holidays.py), so resolving a date never calls out.
Holidays with a fixed date (Christmas Day, Boxing Day, New Year's Day) are plain dates
to the model; these names are for the ones that move, and for "the bank holiday".
"""

import json
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Literal

SNAPSHOT = Path(__file__).with_name("bank_holidays.json")

Holiday = Literal[
    "good_friday",
    "easter_sunday",
    "easter_monday",
    "easter_weekend",
    "early_may_bank_holiday",
    "spring_bank_holiday",
    "summer_bank_holiday",
    "bank_holiday",  # the next bank holiday of any kind
    "bank_holiday_weekend",  # the next long weekend that a bank holiday makes
]

_TITLES = {
    "good_friday": "Good Friday",
    "easter_monday": "Easter Monday",
    "early_may_bank_holiday": "Early May bank holiday",
    "spring_bank_holiday": "Spring bank holiday",
    "summer_bank_holiday": "Summer bank holiday",
}
SATURDAY = 5


@cache
def bank_holidays() -> tuple[tuple[date, str], ...]:
    data = json.loads(SNAPSHOT.read_text())
    return tuple(sorted((date.fromisoformat(e["date"]), e["title"]) for e in data["events"]))


def holiday_days(name: Holiday, today: date) -> tuple[date, date] | None:
    """The first and last day of the next `name` that hasn't ended by `today`, or None
    when the snapshot doesn't reach it."""
    days = bank_holidays()
    match name:
        case "easter_sunday":
            sundays = (d + timedelta(days=2) for d, t in days if t == "Good Friday")
            return next(((d, d) for d in sundays if d >= today), None)
        case "easter_weekend":
            off = {d for d, _ in days}
            blocks = (_days_off(d, off) for d, t in days if t == "Good Friday")
            return next((b for b in blocks if b[1] >= today), None)
        case "bank_holiday":
            return next(((d, d) for d, _ in days if d >= today), None)
        case "bank_holiday_weekend":
            off = {d for d, _ in days}
            for d, _ in days:
                block = _days_off(d, off)
                if block[1] >= today and (block[1] - block[0]).days >= 2:
                    return block
            return None
        case _:
            title = _TITLES[name]
            return next(((d, d) for d, t in days if t == title and d >= today), None)


def _days_off(holiday: date, off: set[date]) -> tuple[date, date]:
    """The run of consecutive weekend days and bank holidays around `holiday`."""

    def is_off(d: date) -> bool:
        return d.weekday() >= SATURDAY or d in off

    first = last = holiday
    while is_off(first - timedelta(days=1)):
        first -= timedelta(days=1)
    while is_off(last + timedelta(days=1)):
        last += timedelta(days=1)
    return first, last
