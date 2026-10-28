"""Shared test helpers for the Understand node. No network: everything here is local.

The city comes from db/seed/cities.sql, areas.sql and calendar.sql and the config from
the migrations, so tests run against what ships, not a hand-made copy.
"""

import json
import re
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.understand.llm import Completion, LLMError, ProviderConfig, Usage
from app.understand.resolve import Area, CalendarDate, City, Point
from app.understand.store import ConfigEntry
from app.understand.tracing import NullTracer, Trace

REPO = Path(__file__).resolve().parents[2]
MIGRATION_014 = REPO / "db" / "migrations" / "014_llm_understand_v2.sql"
MIGRATION_016 = REPO / "db" / "migrations" / "016_search_and_usage.sql"
MIGRATION_018 = REPO / "db" / "migrations" / "018_refine.sql"
MIGRATION_020 = REPO / "db" / "migrations" / "020_date_ranges.sql"
FIXTURES = Path(__file__).parent / "fixtures"

_QUOTED = r"'((?:[^']|'')*)'"
_AREA_ROW = re.compile(
    rf"^\s*\({_QUOTED}, {_QUOTED}, '(borough|neighbourhood|landmark)', "
    r"(array\[.*?\]::text\[\]|'\{\}'::text\[\]), "
    r"'SRID=4326;POINT\(([-\d.]+) ([-\d.]+)\)', ([\d.]+), (?:'E09\d{6}'|null)\)"
)
_CITY_ROW = re.compile(r"'SRID=4326;POINT\(([-\d.]+) ([-\d.]+)\)', ([\d.]+),")
_CALENDAR_ROW = re.compile(
    rf"^\s*\({_QUOTED}, {_QUOTED}, (array\[.*?\]::text\[\]|'\{{\}}'::text\[\]), {_QUOTED}, "
    rf"'([\d-]+)', '([\d-]+)', (true|false), {_QUOTED}, '[\d-]+', ({_QUOTED}|null)\)"
)


def _unquote(value: str) -> str:
    return value.replace("''", "'")


def seed_areas() -> tuple[Area, ...]:
    areas = []
    for line in (REPO / "db" / "seed" / "areas.sql").read_text().splitlines():
        m = _AREA_ROW.match(line)
        if not m:
            continue
        area_id, name, _kind, aliases, lon, lat, radius = m.groups()
        areas.append(
            Area(
                id=_unquote(area_id),
                name=_unquote(name),
                aliases=()
                if aliases.startswith("'{}'")
                else tuple(_unquote(a) for a in re.findall(_QUOTED, aliases)),
                lat=float(lat),
                lon=float(lon),
                default_radius_km=float(radius),
            )
        )
    return tuple(areas)


def seed_calendar() -> tuple[CalendarDate, ...]:
    dates = []
    for line in (REPO / "db" / "seed" / "calendar.sql").read_text().splitlines():
        m = _CALENDAR_ROW.match(line)
        if not m:
            continue
        date_id, name, aliases, _kind, first, last, estimate, _source, area, area_id = m.groups()
        dates.append(
            CalendarDate(
                id=_unquote(date_id),
                name=_unquote(name),
                aliases=()
                if aliases.startswith("'{}'")
                else tuple(_unquote(a) for a in re.findall(_QUOTED, aliases)),
                first=date.fromisoformat(first),
                last=date.fromisoformat(last),
                estimate=estimate == "true",
                area_id=None if area == "null" else _unquote(area_id),
            )
        )
    return tuple(dates)


def london() -> City:
    lon, lat, radius = _CITY_ROW.search((REPO / "db" / "seed" / "cities.sql").read_text()).groups()
    return City(
        id="london",
        name="London",
        tz=ZoneInfo("Europe/London"),
        areas=seed_areas(),
        calendar=seed_calendar(),
        centre=Point(float(lat), float(lon)),
        default_radius_km=float(radius),
    )


def migration_config() -> dict[str, Any]:
    """The active config the migrations ship: llm_understand v2 from 014,
    provider_limits v1 from 016, refine_steps v1 from 018, and understand_prompt v6,
    refine_prompt v3 and date_ranges v1 from 020."""
    llm = re.search(r"\$json\$(.*?)\$json\$", MIGRATION_014.read_text(), re.S).group(1)
    limits = re.search(r"\$json\$(.*?)\$json\$", MIGRATION_016.read_text(), re.S).group(1)
    steps = re.search(
        r"values \('refine_steps', 1, '(.*?)'", MIGRATION_018.read_text(), re.S
    ).group(1)
    m020 = MIGRATION_020.read_text()
    prompt, refine_prompt = re.findall(r"\$prompt\$(.*?)\$prompt\$", m020, re.S)
    ranges = re.search(r"\$json\$(.*?)\$json\$", m020, re.S).group(1)
    return {
        "llm_understand": json.loads(llm),
        "understand_prompt": {"system": prompt},
        "provider_limits": json.loads(limits),
        "refine_prompt": {"system": refine_prompt},
        "refine_steps": json.loads(steps),
        "date_ranges": json.loads(ranges),
    }


def entries(versions: dict[str, int] | None = None, **overrides: Any) -> dict[str, ConfigEntry]:
    values = migration_config() | overrides
    versions = versions or {}
    return {k: ConfigEntry(k, versions.get(k, 1), v) for k, v in values.items()}


def reply(**fields: Any) -> str:
    """A valid UnderstoodQuery JSON reply; every field present, null unless given."""
    base = {
        "keywords": None,
        "categories": None,
        "when": None,
        "place": None,
        "distance": None,
        "price_min": None,
        "price_max": None,
        "price_scope": None,
        "free": False,
        "near_me": False,
        "event": None,
        "vibe": None,
        "who": None,
        "unclear": [],
    }
    return json.dumps(base | fields)


def when(
    kind: str,
    weekday: str | None = None,
    month: int | None = None,
    day: int | None = None,
    qualifier: str | None = None,
    holiday: str | None = None,
    **spans: Any,  # end_month, end_day, season, count, within (decision 0020)
):
    return {
        "kind": kind,
        "weekday": weekday,
        "qualifier": qualifier,
        "month": month,
        "day": day,
        "holiday": holiday,
    } | spans


class FakeStore:
    def __init__(self, config: dict[str, ConfigEntry] | None = None, city: City | None = None):
        self.entries = config if config is not None else entries()
        self.city_value = city or london()
        self.fail: Exception | None = None

    async def config(self, keys):
        if self.fail:
            raise self.fail
        return {k: v for k, v in self.entries.items() if k in keys}

    async def city(self, city_id):
        if self.fail:
            raise self.fail
        return self.city_value


class FakeLLM:
    """Replies per provider, in order. Each reply is reply text or an LLMError."""

    def __init__(self, **script: list[str | LLMError]):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[dict[str, Any]] = []

    async def complete(self, config: ProviderConfig, system, user, schema) -> Completion:
        self.calls.append(
            {
                "provider": config.provider,
                "model": config.model,
                "system": system,
                "user": user,
                "schema": schema,
            }
        )
        item = self.script[config.provider].pop(0)
        if isinstance(item, LLMError):
            raise item
        return Completion(text=item, usage=Usage(120, 40), latency_ms=5)


class RecordingTracer(NullTracer):
    def __init__(self):
        super().__init__(release="test")
        self.sent: list[Trace] = []

    async def send(self, trace: Trace) -> None:
        self.sent.append(trace)
