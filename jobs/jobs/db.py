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
from psycopg.rows import dict_row

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

    def embedding_candidates(self) -> list[dict]:
        """Every event users can see, with the fields its embedding text is built from.

        Duplicates and cancelled events are left out here, because the service role
        bypasses events_public_read (0009).
        """
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                select e.id, e.title, e.category, e.genre, v.name as venue, e.description,
                       e.embedding_hash
                  from public.events e left join public.venues v on v.id = e.venue_id
                 where e.duplicate_of is null and e.status = 'active'
                 order by e.id
                """
            )
            rows = cur.fetchall()
        self.conn.rollback()  # end the read, so each write batch commits on its own
        return rows

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
                  last_checked_at, status, description)
                values (%(city_id)s, %(source)s, %(source_id)s, %(title)s, %(category)s,
                        %(genre)s, %(start_at)s, %(end_at)s, %(venue_id)s, %(price_min)s,
                        %(price_max)s, %(currency)s, %(url)s, %(image_url)s,
                        %(geom)s::extensions.geography, %(checked_at)s, %(status)s,
                        %(description)s)
                on conflict (source, source_id) do update set
                  title = excluded.title, category = excluded.category, genre = excluded.genre,
                  start_at = excluded.start_at, end_at = excluded.end_at,
                  venue_id = excluded.venue_id, price_min = excluded.price_min,
                  price_max = excluded.price_max, currency = excluded.currency,
                  url = excluded.url, image_url = excluded.image_url, geom = excluded.geom,
                  last_checked_at = excluded.last_checked_at, status = excluded.status,
                  -- embedding is left alone: the embedding job compares hashes (0009).
                  description = excluded.description
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
                    "description": event.description,
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

    def delete_ended_events(self, now: datetime, fallback: timedelta) -> tuple[int, int]:
        """Delete ended events. Returns (events, snapshots): the snapshots go by cascade.

        Every part of the statement reads the same snapshot of the data, so the count
        still sees the price_snapshots rows the cascade removes.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                with gone as (
                  delete from public.events
                   where coalesce(end_at, start_at + %(fallback)s) <= %(now)s
                  returning id
                )
                select (select count(*) from gone),
                       (select count(*) from public.price_snapshots
                         where event_id in (select id from gone))
                """,
                {"fallback": fallback, "now": now},
            )
            events, snapshots = cur.fetchone()
            return events, snapshots

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

    # Price snapshots (decision 0009) -----------------------------------------
    # "Current" is the same rule as events_public_read and the delete job; "priced"
    # means price_min or price_max is set.

    def snapshot_cities(self, now: datetime, fallback: timedelta) -> list[tuple[str, ZoneInfo]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select distinct c.id, c.timezone
                  from public.events e join public.cities c on c.id = e.city_id
                 where coalesce(e.end_at, e.start_at + %(fallback)s) > %(now)s
                   and (e.price_min is not null or e.price_max is not null)
                 order by c.id
                """,
                {"fallback": fallback, "now": now},
            )
            return [(city_id, ZoneInfo(tz)) for city_id, tz in cur.fetchall()]

    def count_snapshot_targets(
        self, city_id: str, day: date, now: datetime, fallback: timedelta
    ) -> tuple[int, int]:
        """For a dry run: (events to snapshot, of which already snapshotted today)."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select count(*),
                       count(*) filter (where exists (
                         select 1 from public.price_snapshots s
                          where s.event_id = e.id and s.snapshot_date = %(day)s))
                  from public.events e
                 where e.city_id = %(city)s
                   and coalesce(e.end_at, e.start_at + %(fallback)s) > %(now)s
                   and (e.price_min is not null or e.price_max is not null)
                """,
                {"city": city_id, "day": day, "fallback": fallback, "now": now},
            )
            total, existing = cur.fetchone()
            return total, existing

    def snapshot_prices(
        self, city_id: str, day: date, now: datetime, fallback: timedelta
    ) -> tuple[int, int]:
        """Write today's snapshots for a city. Returns (inserted, updated)."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into public.price_snapshots
                  (event_id, snapshot_date, price_min, price_max, currency, status, captured_at)
                select e.id, %(day)s, e.price_min, e.price_max, e.currency, e.status, %(now)s
                  from public.events e
                 where e.city_id = %(city)s
                   and coalesce(e.end_at, e.start_at + %(fallback)s) > %(now)s
                   and (e.price_min is not null or e.price_max is not null)
                on conflict (event_id, snapshot_date) do update set
                  price_min = excluded.price_min, price_max = excluded.price_max,
                  currency = excluded.currency, status = excluded.status,
                  captured_at = excluded.captured_at
                returning (xmax = 0)
                """,
                {"city": city_id, "day": day, "fallback": fallback, "now": now},
            )
            flags = [row[0] for row in cur.fetchall()]
            return sum(flags), len(flags) - sum(flags)

    # Cross-source duplicates (decision 0009) ----------------------------------

    def dedupe_cities(self) -> list[tuple[str, ZoneInfo]]:
        with self.conn.cursor() as cur:
            cur.execute(
                "select distinct c.id, c.timezone from public.events e"
                " join public.cities c on c.id = e.city_id order by c.id"
            )
            return [(city_id, ZoneInfo(tz)) for city_id, tz in cur.fetchall()]

    def dedupe_candidates(
        self, city_id: str, gap: timedelta, metres: float
    ) -> list[dict[str, object]]:
        """Pairs from different sources in one city, close in time and within `metres`.

        Distance is measured on the geography columns, so it's in metres. Columns are
        named and prefixed a_ and b_, so the two sides never share a name.
        """
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                select a.id a_id, a.source a_source, a.source_id a_source_id,
                       a.title a_title, a.start_at a_start_at, a.status a_status,
                       a.city_id a_city_id, a.end_at a_end_at, a.price_min a_price_min,
                       a.price_max a_price_max, a.currency a_currency,
                       a.image_url a_image_url, a.url a_url, a.genre a_genre,
                       a.venue_id a_venue_id,
                       b.id b_id, b.source b_source, b.source_id b_source_id,
                       b.title b_title, b.start_at b_start_at, b.status b_status,
                       b.city_id b_city_id, b.end_at b_end_at, b.price_min b_price_min,
                       b.price_max b_price_max, b.currency b_currency,
                       b.image_url b_image_url, b.url b_url, b.genre b_genre,
                       b.venue_id b_venue_id,
                       extensions.st_distance(a.geom, b.geom) as metres
                  from public.events a
                  join public.events b
                    on b.city_id = a.city_id
                   and a.source < b.source
                   and b.start_at between a.start_at - %(gap)s and a.start_at + %(gap)s
                   and extensions.st_dwithin(a.geom, b.geom, %(metres)s)
                 where a.city_id = %(city)s
                """,
                {"city": city_id, "gap": gap, "metres": metres},
            )
            return cur.fetchall()

    def current_links(self, city_id: str) -> dict[UUID, UUID]:
        with self.conn.cursor() as cur:
            cur.execute(
                "select id, duplicate_of from public.events"
                " where city_id = %s and duplicate_of is not null",
                (city_id,),
            )
            return dict(cur.fetchall())

    def set_duplicates(self, city_id: str, links: dict[UUID, UUID]) -> None:
        """Replace the city's links: clear every duplicate_of, then set `links`
        (duplicate -> kept). Call inside a transaction so both steps land together."""
        with self.conn.cursor() as cur:
            cur.execute(
                "update public.events set duplicate_of = null"
                " where city_id = %s and duplicate_of is not null",
                (city_id,),
            )
            if links:
                cur.execute(
                    """
                    update public.events e set duplicate_of = v.kept
                      from unnest(%s::uuid[], %s::uuid[]) as v(duplicate, kept)
                     where e.id = v.duplicate and e.city_id = %s
                    """,
                    (list(links), list(links.values()), city_id),
                )

    # OpenStreetMap venues (decision 0009) -------------------------------------

    def set_data_as_of(self, run_id: int, data_as_of: datetime | None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "update public.job_runs set data_as_of = %s where id = %s", (data_as_of, run_id)
            )
        self.conn.commit()  # visible even if the run crashes later

    def diagnostics(self) -> dict[str, object]:
        """Read-only facts about this connection: who it is, where it is, and what it can
        see of venues and job_runs. Used by `jobs.osm_venues --dry-run --diagnose`."""
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                select current_database()                          as database,
                       host(inet_server_addr())                    as server_addr,
                       current_user, session_user,
                       r.rolbypassrls, r.rolsuper,
                       pg_get_userbyid(c.relowner)                 as venues_owner,
                       pg_get_userbyid(c.relowner) = current_user  as owns_venues,
                       c.relrowsecurity                            as venues_rls_enabled,
                       c.relforcerowsecurity                       as venues_rls_forced,
                       (select count(*) from public.venues where source = 'osm')
                                                                   as stored_osm_venues,
                       (select count(*) from public.job_runs)      as job_runs_visible,
                       (select count(*) from public.job_runs where job = 'osm_venues')
                                                                   as osm_job_runs_visible
                  from pg_roles r, pg_class c
                 where r.rolname = current_user and c.oid = 'public.venues'::regclass
                """
            )
            report = dict(cur.fetchone())
            cur.execute(
                """
                select id, started_at, duration_ms, data_as_of, fetched, new, updated, deleted
                  from public.job_runs where job = 'osm_venues'
                 order by started_at desc, id desc limit 5
                """
            )
            report["recent_osm_job_runs"] = [dict(row) for row in cur.fetchall()]
        self.conn.rollback()
        return report

    def osm_venues(self) -> dict[str, tuple]:
        """Stored OSM venues: source_id -> (name, kind, address, booking_url, website_url,
        lat, lon), with coordinates rounded to OSM's 7 decimal places."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select source_id, name, kind, address, booking_url, website_url,
                       round(extensions.st_y(geom::extensions.geometry)::numeric, 7)::float8,
                       round(extensions.st_x(geom::extensions.geometry)::numeric, 7)::float8
                  from public.venues where source = 'osm'
                """
            )
            return {row[0]: tuple(row[1:]) for row in cur.fetchall()}

    def osm_venues_with_curated_pages(self) -> set[str]:
        with self.conn.cursor() as cur:
            cur.execute(
                "select distinct v.source_id from public.venues v"
                " join public.curated_pages c on c.venue_id = v.id where v.source = 'osm'"
            )
            return {row[0] for row in cur.fetchall()}

    def upsert_osm_venues(self, city_id: str, venues: list) -> tuple[int, int]:
        """Insert or update OSM venues in one statement. Rows whose data is unchanged
        aren't touched. Returns (inserted, updated)."""
        if not venues:
            return 0, 0
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into public.venues
                  (city_id, source, source_id, name, kind, address, geom, booking_url, website_url)
                select %(city)s, 'osm', v.source_id, v.name, v.kind, v.address,
                       extensions.st_setsrid(extensions.st_makepoint(v.lon, v.lat), 4326)
                         ::extensions.geography,
                       v.booking_url, v.website_url
                  from unnest(%(ids)s::text[], %(names)s::text[], %(kinds)s::text[],
                              %(addresses)s::text[], %(lons)s::float8[], %(lats)s::float8[],
                              %(bookings)s::text[], %(websites)s::text[])
                       as v(source_id, name, kind, address, lon, lat, booking_url, website_url)
                on conflict (source, source_id) do update set
                  name = excluded.name, kind = excluded.kind, address = excluded.address,
                  geom = excluded.geom, booking_url = excluded.booking_url,
                  website_url = excluded.website_url
                 where (venues.name, venues.kind, venues.address, venues.booking_url,
                        venues.website_url, extensions.st_asbinary(venues.geom))
                       is distinct from
                       (excluded.name, excluded.kind, excluded.address, excluded.booking_url,
                        excluded.website_url, extensions.st_asbinary(excluded.geom))
                returning (xmax = 0)
                """,
                {
                    "city": city_id,
                    "ids": [v.source_id for v in venues],
                    "names": [v.name for v in venues],
                    "kinds": [v.kind for v in venues],
                    "addresses": [v.address for v in venues],
                    "lons": [v.lon for v in venues],
                    "lats": [v.lat for v in venues],
                    "bookings": [v.booking_url for v in venues],
                    "websites": [v.website_url for v in venues],
                },
            )
            flags = [row[0] for row in cur.fetchall()]
            return sum(flags), len(flags) - sum(flags)

    def delete_vanished_osm_venues(self, seen: list[str]) -> tuple[int, int]:
        """Delete OSM venues not in `seen`, except those with curated pages: the cascade
        from venues (001_core.sql:128) would delete the pages too.
        Returns (deleted, kept because they have curated pages)."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                with vanished as (
                  select v.id,
                         exists (select 1 from public.curated_pages c
                                  where c.venue_id = v.id) as has_pages
                    from public.venues v
                   where v.source = 'osm' and v.source_id <> all(%(seen)s::text[])
                ),
                gone as (
                  delete from public.venues v using vanished x
                   where v.id = x.id and not x.has_pages
                  returning v.id
                )
                select (select count(*) from gone),
                       (select count(*) from vanished where has_pages)
                """,
                {"seen": seen},
            )
            deleted, kept = cur.fetchone()
            return deleted, kept

    def set_embedding(self, event_id: UUID, vector: list[float], text_hash: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                update public.events
                   set embedding = %s::extensions.vector, embedding_hash = %s
                 where id = %s
                """,
                (_vector(vector), text_hash, event_id),
            )


def _point(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    return f"SRID=4326;POINT({lon} {lat})"


def _vector(values: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"
