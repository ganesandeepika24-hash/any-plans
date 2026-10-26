"""Database access over a direct Postgres connection (Supabase session pooler).

The jobs connect as the tables' owner, which RLS doesn't restrict (it isn't forced).
A read-only Database opens every transaction READ ONLY, so a dry run can't write
even if a code path tries to.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from jobs.normalise import Event, Venue


@dataclass(frozen=True)
class City:
    id: str
    tz: ZoneInfo
    lat: float
    lon: float


@dataclass
class RunCounts:
    fetched: int = 0
    new: int = 0
    updated: int = 0
    failed: int = 0
    deleted: int = 0


class Database:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    @classmethod
    def connect(cls, url: str, read_only: bool) -> "Database":
        conn = psycopg.connect(url, autocommit=False)
        conn.read_only = read_only
        return cls(conn)

    @property
    def read_only(self) -> bool:
        return bool(self.conn.read_only)

    def close(self) -> None:
        self.conn.close()

    # Reads ----------------------------------------------------------------

    def city(self, city_id: str) -> City:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select id, timezone,
                       extensions.st_y(centre_geom::extensions.geometry),
                       extensions.st_x(centre_geom::extensions.geometry)
                  from public.cities where id = %s
                """,
                (city_id,),
            )
            row = cur.fetchone()
        self.conn.rollback()
        if row is None:
            raise LookupError(f"no city {city_id!r}")
        return City(row[0], ZoneInfo(row[1]), row[2], row[3])

    def calls_today(self, service: str, today: date) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "select calls from public.usage_daily where date = %s and service = %s",
                (today, service),
            )
            row = cur.fetchone()
        self.conn.rollback()
        return row[0] if row and row[0] is not None else 0

    def events_missing_embedding(self, limit: int = 500) -> list[tuple[UUID, str]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select e.id, concat_ws(' · ', e.title, e.genre, v.name)
                  from public.events e left join public.venues v on v.id = e.venue_id
                 where e.embedding is null
                 limit %s
                """,
                (limit,),
            )
            return cur.fetchall()

    # Run records ----------------------------------------------------------

    def start_run(self, job: str, city_id: str | None, started_at: datetime) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "insert into public.job_runs (job, city_id, started_at) values (%s, %s, %s)"
                " returning id",
                (job, city_id, started_at),
            )
            run_id = cur.fetchone()[0]
        self.conn.commit()  # visible even if the run crashes later
        return run_id

    def finish_run(self, run_id: int, duration: timedelta, counts: RunCounts) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                update public.job_runs
                   set duration_ms = %s, fetched = %s, new = %s, updated = %s,
                       failed = %s, deleted = %s
                 where id = %s
                """,
                (
                    int(duration.total_seconds() * 1000),
                    counts.fetched,
                    counts.new,
                    counts.updated,
                    counts.failed,
                    counts.deleted,
                    run_id,
                ),
            )
        self.conn.commit()

    def add_calls(self, service: str, today: date, calls: int, daily_limit: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into public.usage_daily (date, service, calls, daily_limit)
                values (%s, %s, %s, %s)
                on conflict (date, service) do update
                   set calls = coalesce(public.usage_daily.calls, 0) + excluded.calls,
                       daily_limit = excluded.daily_limit
                """,
                (today, service, calls, daily_limit),
            )
        self.conn.commit()

    # Writes ---------------------------------------------------------------

    def upsert_venue(self, venue: Venue, city_id: str) -> UUID:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into public.venues (city_id, source, source_id, name, kind, address, geom)
                values (%s, %s, %s, %s, 'other', %s, %s::extensions.geography)
                on conflict (source, source_id) do update
                   set name = excluded.name, address = excluded.address, geom = excluded.geom
                returning id
                """,
                (
                    city_id,
                    venue.source,
                    venue.source_id,
                    venue.name,
                    venue.address,
                    _point(venue.lat, venue.lon),
                ),
            )
            return cur.fetchone()[0]

    def upsert_event(
        self, event: Event, city_id: str, venue_id: UUID | None, checked_at: datetime
    ) -> bool:
        """Insert or update one event. Returns True if the row is new."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into public.events (
                  city_id, source, source_id, title, category, genre, start_at, end_at,
                  venue_id, price_min, price_max, currency, url, image_url, geom,
                  last_checked_at, status)
                values (%(city_id)s, %(source)s, %(source_id)s, %(title)s, %(category)s,
                        %(genre)s, %(start_at)s, %(end_at)s, %(venue_id)s, %(price_min)s,
                        %(price_max)s, %(currency)s, %(url)s, %(image_url)s,
                        %(geom)s::extensions.geography, %(checked_at)s, %(status)s)
                on conflict (source, source_id) do update set
                  title = excluded.title, category = excluded.category, genre = excluded.genre,
                  start_at = excluded.start_at, end_at = excluded.end_at,
                  venue_id = excluded.venue_id, price_min = excluded.price_min,
                  price_max = excluded.price_max, currency = excluded.currency,
                  url = excluded.url, image_url = excluded.image_url, geom = excluded.geom,
                  last_checked_at = excluded.last_checked_at, status = excluded.status,
                  -- The embedding text is title, genre and venue; re-embed when they change.
                  embedding = case
                    when events.title is distinct from excluded.title
                      or events.genre is distinct from excluded.genre
                      or events.venue_id is distinct from excluded.venue_id
                    then null else events.embedding end
                returning (xmax = 0)
                """,
                {
                    "city_id": city_id,
                    "source": event.source,
                    "source_id": event.source_id,
                    "title": event.title,
                    "category": event.category,
                    "genre": event.genre,
                    "start_at": event.start_at,
                    "end_at": event.end_at,
                    "venue_id": venue_id,
                    "price_min": event.price_min,
                    "price_max": event.price_max,
                    "currency": event.currency,
                    "url": event.url,
                    "image_url": event.image_url,
                    "geom": _point(event.lat, event.lon),
                    "checked_at": checked_at,
                    "status": event.status,
                },
            )
            return bool(cur.fetchone()[0])

    def delete_events(self, source: str, source_ids: list[str]) -> int:
        if not source_ids:
            return 0
        with self.conn.cursor() as cur:
            cur.execute(
                "delete from public.events where source = %s and source_id = any(%s)",
                (source, source_ids),
            )
            return cur.rowcount

    def sweep_vanished(
        self, source: str, horizon_start: datetime, horizon_end: datetime, run_started: datetime
    ) -> int:
        """Delete events in the fetched horizon that this run didn't see."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                delete from public.events
                 where source = %s and start_at >= %s and start_at < %s
                   and (last_checked_at is null or last_checked_at < %s)
                """,
                (source, horizon_start, horizon_end, run_started),
            )
            return cur.rowcount

    def delete_ended_events(self, now: datetime, fallback: timedelta) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "delete from public.events where coalesce(end_at, start_at + %s) <= %s",
                (fallback, now),
            )
            return cur.rowcount

    def delete_orphan_venues(self, source: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                delete from public.venues v
                 where v.source = %s
                   and not exists (select 1 from public.events e where e.venue_id = v.id)
                """,
                (source,),
            )
            return cur.rowcount

    def set_embedding(self, event_id: UUID, vector: list[float]) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "update public.events set embedding = %s::extensions.vector where id = %s",
                (_vector(vector), event_id),
            )


def _point(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    return f"SRID=4326;POINT({lon} {lat})"


def _vector(values: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"
