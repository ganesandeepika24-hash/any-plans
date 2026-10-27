"""The /parse service: daily cap, the Understand step, and the queries row (decision 0013).

1. Check the user's daily cap (config ai_search_cap, counted from today's queries rows
   since midnight UTC). At the cap, no model is called and the reply is ai_resting.
2. Run the Understand step. A request may carry the user's own point (a pin, or their
   location for "near me"); it's used for that search only, and never logged.
3. Build the queries row. The route writes it after the response is sent; a failed
   write is logged and never fails the request.

The cap check and its reservation run under one lock, and a reservation lasts until
the row is written, so parallel requests from one user can't slip past the cap. That
holds within one process; Render runs one (decision 0004). More instances would need
the reservation in the database.
"""

import asyncio
import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from datetime import time as clock_time
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.querylog import QueryLog, QueryLogError
from app.understand.node import AI_RESTING, MAX_QUERY_CHARS, Understander, stored_filters
from app.understand.resolve import Point
from app.understand.store import Store, StoreError

log = logging.getLogger(__name__)

CAP_KEY = "ai_search_cap"
CAPPED = "Daily AI search limit reached; filters still work"

Reason = Literal["providers", "daily_cap", "unavailable"]


class CapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)  # "10" is a config mistake

    per_user_per_day: int = Field(ge=0, le=1000)


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    location: Location | None = None  # the user's pin or position, for this search only

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query is blank")
        return value


class NoteOut(BaseModel):
    code: str
    message: str


class QuestionOut(BaseModel):
    reason: str
    question: str
    choices: list[str]


class ParseResponse(BaseModel):
    status: Literal["ok", "needs_clarification", "ai_resting"]
    reason: Reason | None = None  # set when status is ai_resting
    message: str | None = None
    query_id: UUID
    filters: dict[str, Any] | None = None  # search_items' jsonb argument
    embedding_text: str | None = None
    notes: list[NoteOut] = []
    missing: list[str] = []  # set when status is needs_clarification
    questions: list[QuestionOut] = []  # one per missing reason, in the same order
    trace_id: str | None = None


@dataclass
class ParseOutcome:
    response: ParseResponse
    row: dict[str, Any]
    reservation: tuple[UUID, date] | None  # released when the row is written


class Parser:
    def __init__(
        self,
        understander: Understander,
        store: Store,
        query_log: QueryLog,
        release: str,
        city_id: str = "london",
        clock=lambda: datetime.now(UTC),
        monotonic=time.monotonic,
    ):
        self._understander = understander
        self._store = store
        self._log = query_log
        self._release = release
        self._city_id = city_id
        self._clock = clock
        self._monotonic = monotonic
        self._admit_lock = asyncio.Lock()
        self._pending: Counter[tuple[UUID, date]] = Counter()

    async def parse(
        self, user_id: UUID, query: str, location: Location | None = None
    ) -> ParseOutcome:
        started = self._monotonic()
        query_id = uuid4()
        day = self._clock().astimezone(UTC).date()

        async with self._admit_lock:
            try:
                entry = (await self._store.config((CAP_KEY,)))[CAP_KEY]
                cap = CapConfig.model_validate(entry.value).per_user_per_day
                since = datetime.combine(day, clock_time(0), tzinfo=UTC)
                used = await self._log.count_today(user_id, since)
            except (StoreError, QueryLogError, ValidationError, KeyError) as exc:
                log.warning("daily cap unavailable: %s", exc)
                return self._resting(query_id, user_id, query, started, "unavailable", AI_RESTING)
            cap_versions = {CAP_KEY: entry.version}
            if used + self._pending[(user_id, day)] >= cap:
                return self._resting(
                    query_id, user_id, query, started, "daily_cap", CAPPED, cap_versions
                )
            self._pending[(user_id, day)] += 1
        reservation = (user_id, day)

        try:
            point = Point(location.lat, location.lon) if location is not None else None
            result = await self._understander.run(query, point)
        except BaseException:
            self._release_reservation(reservation)
            raise

        notes = [NoteOut(**asdict(n)) for n in result.notes]
        filters = result.filters.to_json() if result.filters else None
        missing = list(result.missing)
        reason: Reason | None = None
        if result.status == "ai_resting":
            reason = "providers" if result.attempts else "unavailable"
        response = ParseResponse(
            status=result.status,
            reason=reason,
            message=result.message,
            query_id=query_id,
            filters=filters,
            embedding_text=result.query_text,
            notes=notes,
            missing=missing,
            questions=[
                QuestionOut(reason=q.reason, question=q.question, choices=list(q.choices))
                for q in result.questions
            ],
            trace_id=result.trace_id,
        )
        row = self._row(
            query_id,
            user_id,
            query,
            started,
            status=result.status,
            provider=result.provider,
            attempts=[
                {"provider": a.provider, "model": a.model, "outcome": a.outcome}
                for a in result.attempts
            ],
            filters=stored_filters(result),
            embedding_text=result.query_text,
            notes=[n.model_dump() for n in notes],
            missing=missing,
            config_versions=result.config_versions | cap_versions,
            trace_id=result.trace_id,
        )
        return ParseOutcome(response, row, reservation)

    async def record(self, outcome: ParseOutcome) -> None:
        """Write the queries row, then release the cap reservation. Never raises."""
        try:
            await self._log.write(outcome.row)
        except QueryLogError as exc:
            log.warning("query %s not logged: %s", outcome.row["id"], exc)
        finally:
            self._release_reservation(outcome.reservation)

    def _release_reservation(self, reservation: tuple[UUID, date] | None) -> None:
        if reservation is None:
            return
        self._pending[reservation] -= 1
        if self._pending[reservation] <= 0:
            del self._pending[reservation]

    def _resting(
        self,
        query_id: UUID,
        user_id: UUID,
        query: str,
        started: float,
        reason: Reason,
        message: str,
        config_versions: dict[str, int] | None = None,
    ) -> ParseOutcome:
        response = ParseResponse(
            status="ai_resting", reason=reason, message=message, query_id=query_id
        )
        status = "capped" if reason == "daily_cap" else "ai_resting"
        row = self._row(
            query_id,
            user_id,
            query,
            started,
            status=status,
            config_versions=config_versions or {},
        )
        return ParseOutcome(response, row, None)

    def _row(
        self,
        query_id: UUID,
        user_id: UUID,
        query: str,
        started: float,
        *,
        status: str,
        provider: str | None = None,
        attempts: list[dict[str, str]] | None = None,
        filters: dict[str, Any] | None = None,
        embedding_text: str | None = None,
        notes: list[dict[str, str]] | None = None,
        missing: list[str] | None = None,
        config_versions: dict[str, int],
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """A queries row (migration 002, plus missing from 013; columns proposed in 0013)."""
        return {
            "id": str(query_id),
            "user_id": str(user_id),
            "city_id": self._city_id,
            "route": "parse",
            "query_text": query,
            "status": status,
            "provider": provider,
            "attempts": attempts or [],
            "filters": filters,
            "embedding_text": embedding_text,
            "notes": notes or [],
            "missing": missing or [],
            "config_versions": config_versions,
            "release": self._release,
            "trace_id": trace_id,
            "latency_ms": int((self._monotonic() - started) * 1000),
        }
