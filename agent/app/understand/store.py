"""Config and city context from Supabase, through PostgREST with the service key.

Config is reread every minute, so a set_active_config switch reaches the agent within
a minute and without a redeploy (principle 8). City context (time zone and areas)
changes only with the seeds, so it's kept for an hour. If a refresh fails, the last
good value is used; with none, the error propagates.
"""

import time
from dataclasses import dataclass
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from app.understand.resolve import Area, City

CONFIG_TTL_S = 60.0
CONTEXT_TTL_S = 3600.0
TIMEOUT_S = 5.0


@dataclass(frozen=True)
class ConfigEntry:
    key: str
    version: int
    value: Any


class Store(Protocol):
    async def config(self, keys: tuple[str, ...]) -> dict[str, ConfigEntry]: ...

    async def city(self, city_id: str) -> City: ...


class StoreError(Exception):
    pass


class SupabaseStore:
    def __init__(
        self,
        http: httpx.AsyncClient,
        rest_url: str,
        service_key: str | None,
        clock=time.monotonic,
    ):
        self._http = http
        self._rest = rest_url.rstrip("/")
        self._key = service_key
        self._clock = clock
        self._config: dict[tuple[str, ...], tuple[float, dict[str, ConfigEntry]]] = {}
        self._cities: dict[str, tuple[float, City]] = {}

    async def config(self, keys: tuple[str, ...]) -> dict[str, ConfigEntry]:
        cached = self._config.get(keys)
        if cached and self._clock() - cached[0] < CONFIG_TTL_S:
            return cached[1]
        try:
            rows = await self._get(
                "/config",
                {
                    "select": "key,version,value_json",
                    "is_active": "is.true",
                    "key": f"in.({','.join(keys)})",
                },
            )
            entries = {r["key"]: ConfigEntry(r["key"], r["version"], r["value_json"]) for r in rows}
            missing = sorted(set(keys) - set(entries))
            if missing:
                raise StoreError(f"no active config for {missing}")
        except (StoreError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            if cached:
                return cached[1]
            raise StoreError(f"config unavailable: {exc}") from exc
        self._config[keys] = (self._clock(), entries)
        return entries

    async def city(self, city_id: str) -> City:
        cached = self._cities.get(city_id)
        if cached and self._clock() - cached[0] < CONTEXT_TTL_S:
            return cached[1]
        try:
            context = await self._post("/rpc/understand_context", {"city_id": city_id})
            if context is None:
                raise StoreError(f"no city {city_id!r}")
            city = city_from_context(context)
        except (StoreError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            if cached:
                return cached[1]
            raise StoreError(f"city context unavailable: {exc}") from exc
        self._cities[city_id] = (self._clock(), city)
        return city

    async def _get(self, path: str, params: dict[str, str]) -> Any:
        response = await self._http.get(
            self._rest + path, params=params, headers=self._headers(), timeout=TIMEOUT_S
        )
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        response = await self._http.post(
            self._rest + path, json=body, headers=self._headers(), timeout=TIMEOUT_S
        )
        response.raise_for_status()
        return response.json()

    def _headers(self) -> dict[str, str]:
        if not self._key:
            raise StoreError("SUPABASE_SERVICE_KEY is not set")
        return {"apikey": self._key, "Authorization": f"Bearer {self._key}"}


def city_from_context(context: dict[str, Any]) -> City:
    """Build a City from understand_context's jsonb (migration 011)."""
    areas = tuple(
        Area(
            id=a["id"],
            name=a["name"],
            aliases=tuple(a.get("aliases") or ()),
            lat=float(a["lat"]),
            lon=float(a["lon"]),
            default_radius_km=float(a["default_radius_km"]),
        )
        for a in context["areas"]
    )
    city = context["city"]
    return City(id=city["id"], name=city["name"], tz=ZoneInfo(city["timezone"]), areas=areas)
