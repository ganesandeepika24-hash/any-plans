"""The queries table through PostgREST with the service key (migration 002, decision 0013).

count_today backs the daily cap; write records one row per request. query_results is
written by the search route, not here: /parse shows no events.
"""

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

import httpx

TIMEOUT_S = 5.0
COUNTED_STATUSES = ("ok", "ai_resting")  # capped requests don't count against the cap


class QueryLogError(Exception):
    pass


class QueryLog(Protocol):
    async def count_today(self, user_id: UUID, since: datetime) -> int:
        """Rows for user_id since `since` that count against the cap."""
        ...

    async def write(self, row: dict[str, Any]) -> None: ...


class SupabaseQueryLog:
    def __init__(self, http: httpx.AsyncClient, rest_url: str, service_key: str | None):
        self._http = http
        self._url = rest_url.rstrip("/") + "/queries"
        self._key = service_key

    async def count_today(self, user_id: UUID, since: datetime) -> int:
        try:
            response = await self._http.get(
                self._url,
                params={
                    "select": "id",
                    "user_id": f"eq.{user_id}",
                    "created_at": f"gte.{since.isoformat()}",
                    "status": f"in.({','.join(COUNTED_STATUSES)})",
                    "limit": "1",
                },
                headers=self._headers() | {"Prefer": "count=exact"},
                timeout=TIMEOUT_S,
            )
            response.raise_for_status()
            # PostgREST reports the total after the slash: "0-0/7", or "*/0" for none.
            return int(response.headers["content-range"].rsplit("/", 1)[1])
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            raise QueryLogError(f"couldn't count today's queries: {type(exc).__name__}") from exc

    async def write(self, row: dict[str, Any]) -> None:
        try:
            response = await self._http.post(
                self._url,
                json=row,
                headers=self._headers() | {"Prefer": "return=minimal"},
                timeout=TIMEOUT_S,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise QueryLogError(f"couldn't write the queries row: {type(exc).__name__}") from exc

    def _headers(self) -> dict[str, str]:
        if not self._key:
            raise QueryLogError("SUPABASE_SERVICE_KEY is not set")
        return {"apikey": self._key, "Authorization": f"Bearer {self._key}"}
