"""Shared test helpers for the Understand node. No network: everything here is local.

The city comes from db/seed/areas.sql and the config from the migrations (models from
011, the active prompt from 013), so tests run against what ships, not a hand-made copy.
"""

import json
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.understand.llm import Completion, LLMError, ProviderConfig, Usage
from app.understand.resolve import Area, City
from app.understand.store import ConfigEntry
from app.understand.tracing import NullTracer, Trace

REPO = Path(__file__).resolve().parents[2]
MIGRATION_014 = REPO / "db" / "migrations" / "014_llm_understand_v2.sql"
MIGRATION_016 = REPO / "db" / "migrations" / "016_search_and_usage.sql"
MIGRATION_017 = REPO / "db" / "migrations" / "017_near_me_prompt.sql"
FIXTURES = Path(__file__).parent / "fixtures"

_QUOTED = r"'((?:[^']|'')*)'"
_AREA_ROW = re.compile(
    rf"^\s*\({_QUOTED}, {_QUOTED}, '(borough|neighbourhood)', "
    r"(array\[.*?\]::text\[\]|'\{\}'::text\[\]), "
    r"'SRID=4326;POINT\(([-\d.]+) ([-\d.]+)\)', ([\d.]+)\)"
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


def london() -> City:
    return City(id="london", name="London", tz=ZoneInfo("Europe/London"), areas=seed_areas())


def migration_config() -> dict[str, Any]:
    """The active config the migrations ship: llm_understand v2 from 014,
    understand_prompt v4 from 017 and provider_limits v1 from 016."""
    llm = re.search(r"\$json\$(.*?)\$json\$", MIGRATION_014.read_text(), re.S).group(1)
    prompt = re.search(r"\$prompt\$(.*?)\$prompt\$", MIGRATION_017.read_text(), re.S).group(1)
    limits = re.search(r"\$json\$(.*?)\$json\$", MIGRATION_016.read_text(), re.S).group(1)
    return {
        "llm_understand": json.loads(llm),
        "understand_prompt": {"system": prompt},
        "provider_limits": json.loads(limits),
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
):
    return {
        "kind": kind,
        "weekday": weekday,
        "qualifier": qualifier,
        "month": month,
        "day": day,
        "holiday": holiday,
    }


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
