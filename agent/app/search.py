"""POST /search and POST /refine: the Understand or Refine step, then the Search node
(stage 1 steps 1 and 2, decisions 0016 and 0018).

1. The parser runs as for /parse, under the same daily cap, and writes a queries row
   with route 'search'.
2. Only an ok parse reaches the Search node. needs_clarification and ai_resting answer as
   /parse does, with no search.
3. The Search node embeds the search text (in process, fastembed) and calls search_items
   (migration 010) with the filters. vibe and who aren't passed: search_items takes only
   SearchFilters' keys and rejects others (decision 0015). `windows` ("the next 4
   Saturdays", decision 0020) is the agent's own: the node asks search_items for its
   most rows across the whole span, keeps the events on during a window, then cuts back
   to the limit, so weekday events can't fill the limit first.
4. /refine reads the user's own earlier queries row first, and runs the Refine step on
   its filters instead of Understand. Everything after that is the same.
5. The events shown are logged to query_results by source and source_id (decision 0007),
   after the queries row. The user's location is in neither: queries keeps filters
   without the user's own point, and query_results holds no distances.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from app.parse import Location, ParseOutcome, Parser, ParseResponse
from app.querylog import QueryLog, QueryLogError
from app.understand.refine import Prior

log = logging.getLogger(__name__)

TIMEOUT_S = 10.0
SEARCH_ITEMS_LIMIT = 20  # search_items' default limit (010)
SEARCH_ITEMS_MAX = 100  # and its most, fetched when windows narrow the results
SEARCH_UNAVAILABLE = "Search is unavailable right now. Try again in a moment."


class SearchError(Exception):
    pass


class PriorNotFound(Exception):
    """No such query, or it's another user's: the same answer for both."""


class PriorNotRefinable(Exception):
    """The earlier query didn't resolve (it asked a question, rested or was capped)."""


class SearchBackend(Protocol):
    async def search(
        self, filters: dict[str, Any], embedding: list[float] | None
    ) -> list[dict[str, Any]]:
        """search_items' rows, best first."""
        ...

    async def source_ids(self, event_ids: list[str]) -> dict[str, str]:
        """events.source_id for each id."""
        ...


class SupabaseSearch:
    """search_items and an events lookup through PostgREST with the service key."""

    def __init__(self, http: httpx.AsyncClient, rest_url: str, service_key: str | None):
        self._http = http
        self._rest = rest_url.rstrip("/")
        self._key = service_key

    async def search(
        self, filters: dict[str, Any], embedding: list[float] | None
    ) -> list[dict[str, Any]]:
        body = {"filters": filters, "query_embedding": _vector(embedding) if embedding else None}
        rows = await self._request("POST", "/rpc/search_items", json=body)
        if not isinstance(rows, list):
            raise SearchError("search_items didn't return rows")
        return rows

    async def source_ids(self, event_ids: list[str]) -> dict[str, str]:
        if not event_ids:
            return {}
        rows = await self._request(
            "GET",
            "/events",
            params={"select": "id,source_id", "id": f"in.({','.join(event_ids)})"},
        )
        return {r["id"]: r["source_id"] for r in rows}

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        if not self._key:
            raise SearchError("SUPABASE_SERVICE_KEY is not set")
        try:
            response = await self._http.request(
                method,
                self._rest + path,
                headers={"apikey": self._key, "Authorization": f"Bearer {self._key}"},
                timeout=TIMEOUT_S,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise SearchError(f"{path} failed: {type(exc).__name__}") from exc


def _vector(values: list[float]) -> str:
    """pgvector's text form, as jobs/db.py writes embeddings."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


Confidence = Literal["checked_today", "check_site", "web"]


class ResultOut(BaseModel):
    rank: int
    id: UUID
    source: str
    title: str
    category: str | None
    genre: str | None
    start_at: datetime
    end_at: datetime | None
    venue_name: str | None
    price_min: float | None
    price_max: float | None
    currency: str | None
    url: str | None
    image_url: str | None
    last_checked_at: datetime | None  # freshness, shown on every card (principle 6)
    # The card's confidence label, from source and last_checked_at in search_items
    # (migration 022, decision 0022), never from the model. Required: without 022 every
    # row fails here and the search answers SEARCH_UNAVAILABLE.
    confidence: Confidence
    confidence_label: str
    distance_m: float | None  # from the searched point; returned, never logged
    lat: float | None  # the event's own point, for the map; null when it has none
    lon: float | None
    score: float


@dataclass(frozen=True)
class Hit:
    result: ResultOut
    source_id: str


class SearchNode:
    """Filters and search text in, ranked events out. Raises SearchError."""

    def __init__(self, backend: SearchBackend, embed: Callable[[list[str]], list[list[float]]]):
        self._backend = backend
        self._embed = embed

    async def run(self, filters: dict[str, Any], query_text: str | None) -> list[Hit]:
        embedding = None
        if query_text:
            try:
                # CPU-bound: off the event loop.
                (embedding,) = await asyncio.to_thread(self._embed, [query_text])
            except Exception as exc:  # the model failing to load or run
                raise SearchError(f"embedding failed: {type(exc).__name__}") from exc
        filters = dict(filters)
        windows = filters.pop("windows", None)
        if windows is not None:
            limit = filters.get("limit", SEARCH_ITEMS_LIMIT)
            filters["limit"] = SEARCH_ITEMS_MAX
            spans = [(_moment(a), _moment(b)) for a, b in windows]
        rows = await self._backend.search(filters, embedding)
        if windows is not None:
            rows = [r for r in rows if _on_during(r, spans)][:limit]
        ids = await self._backend.source_ids([str(r["id"]) for r in rows])
        hits = []
        for row in rows:
            source_id = ids.get(str(row["id"]))
            if source_id is None:
                continue  # deleted between the search and the lookup
            fields = {k: row.get(k) for k in ResultOut.model_fields if k != "rank"}
            try:
                hits.append(Hit(ResultOut(rank=len(hits) + 1, **fields), source_id))
            except ValidationError as exc:
                raise SearchError(
                    f"search_items row didn't validate: {exc.error_count()} errors"
                ) from exc
        return hits


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _on_during(row: dict[str, Any], spans: list[tuple[datetime, datetime]]) -> bool:
    """Whether the event is on during one of the spans: it starts in one, or it's running
    across one. An event with no end is the moment it starts."""
    try:
        start = _moment(row["start_at"])
        end = _moment(row["end_at"]) if row.get("end_at") else start
    except (KeyError, TypeError, ValueError) as exc:
        raise SearchError(f"search_items row has no usable start_at: {exc}") from exc
    return any(start < b and end >= a for a, b in spans)


class SearchResponse(ParseResponse):
    results: list[ResultOut] | None = None  # set when the parse was ok and search ran


@dataclass
class SearchOutcome:
    response: SearchResponse
    parsed: ParseOutcome
    result_rows: list[dict[str, Any]] = field(default_factory=list)


class Searcher:
    def __init__(self, parser: Parser, node: SearchNode, query_log: QueryLog):
        self._parser = parser
        self._node = node
        self._log = query_log

    async def search(
        self, user_id: UUID, query: str, location: Location | None = None
    ) -> SearchOutcome:
        parsed = await self._parser.parse(user_id, query, location, route="search")
        return await self._searched(parsed)

    async def refine(
        self, user_id: UUID, prior_id: UUID, message: str, location: Location | None = None
    ) -> SearchOutcome:
        """The user's earlier query, changed by `message`, then searched (decision 0018).
        Raises PriorNotFound, PriorNotRefinable or QueryLogError before any model call."""
        row = await self._log.get_own(prior_id, user_id)
        if row is None:
            raise PriorNotFound(prior_id)
        if row["status"] != "ok" or row["filters"] is None:
            raise PriorNotRefinable(row["status"])
        prior = Prior(row["filters"], row["embedding_text"], row.get("vibe"), row.get("who"))
        parsed = await self._parser.refine(user_id, prior_id, prior, message, location)
        return await self._searched(parsed)

    async def _searched(self, parsed: ParseOutcome) -> SearchOutcome:
        response = SearchResponse(**parsed.response.model_dump())
        if response.status != "ok":
            return SearchOutcome(response, parsed)  # never searched: answer like /parse
        try:
            hits = await self._node.run(response.filters or {}, response.embedding_text)
        except SearchError as exc:
            log.warning("search %s failed: %s", response.query_id, exc)
            response.message = SEARCH_UNAVAILABLE
            return SearchOutcome(response, parsed)
        response.results = [h.result for h in hits]
        rows = [
            {
                "query_id": str(response.query_id),
                "rank": h.result.rank,
                "source": h.result.source,
                "source_id": h.source_id,
                "score": h.result.score,
            }
            for h in hits
        ]
        return SearchOutcome(response, parsed, rows)

    async def record(self, outcome: SearchOutcome) -> None:
        """The queries row first (query_results references it), then the results.
        Never raises."""
        await self._parser.record(outcome.parsed)
        if not outcome.result_rows:
            return
        try:
            await self._log.write_results(outcome.result_rows)
        except QueryLogError as exc:
            log.warning("results for %s not logged: %s", outcome.response.query_id, exc)
