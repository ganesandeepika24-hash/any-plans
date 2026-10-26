# 0006: Core schema (migration 001)

Status: accepted (2026-10-26). Owner: deepika. Revisit: before migration 002 (profiles and queries), or when a second city is added.

## Context

Step 0.5 creates the tables every later stage reads from: cities, areas, venues, events, curated pages, job runs, daily usage and config. The column list was fixed in the "Final spec: migration 001", and this record covers the choices around it. Those choices are extensions, migration tracking, indexes, privileges and Row Level Security, seeds, and CI. The schema must apply to a fresh Postgres in CI and to Supabase from the same file, and the public (`anon`) key must only ever read what the site shows.

## Options considered

| Area | Options | Choice | Why |
|---|---|---|---|
| Extension schema | `public`; **`extensions`** | `extensions` | Matches Supabase, and keeps PostGIS's `spatial_ref_sys` out of the API schema. Types and operator classes are schema-qualified, so nothing depends on `search_path`. 001 fails with a clear message if postgis or vector already live in another schema. |
| pg_cron | Required; **optional with a NOTICE** | Optional | Available on Supabase, absent from the CI image. One file works in both. |
| Migration tracking | None; Supabase CLI; **own `schema_migrations` plus `db/ci/migrate.sh`** | Own table and runner | 001 refuses to run if version 001 is recorded. The runner stores each file's sha256 and fails if an applied file changes. On PRs, CI also fails if a file under `db/migrations/` is modified, renamed or deleted. |
| Config versions | Partial unique index; deferrable exclusion constraint | **Partial unique index** on `(key) where is_active`, plus `set_active_config(key, version)` | The index is checked row by row, so a single UPDATE that swaps two rows can fail. The function locks the key's rows, deactivates the current version, then activates the new one, all in the caller's transaction. Only `service_role` may execute it. |
| Events with no end time | Hide immediately; show forever; **start plus 3 hours** | `coalesce(end_at, start_at + interval '3 hours') > now()` | Ticketmaster often has no end time. Ingest must set `end_at` for multi-day events, or they disappear 3 hours after opening. |
| Privileges | RLS only; **explicit revoke and grant plus RLS** | Both | See "Supabase default privileges" below. |
| CI image | `postgis/postgis:17-3.5`; **`postgis/postgis:17-3.6`** | 17-3.6 | 17-3.5 was approved first but can't install pgvector (see results). |

## Decision

- `db/migrations/001_core.sql` creates `schema_migrations` and the eight spec tables, plus one column the spec didn't have: `areas.kind` (`borough` or `neighbourhood`), approved during planning so Camden (borough) and Camden Town (neighbourhood) can be told apart.
- Constraints added during planning: `NOT NULL` on the core fields (`name`, `title`, `source`, `source_id`, `start_at`, `status`, `kind`, `category`) and on `city_id` in areas, venues and events. `CHECK` lists for `venues.kind`, `events.category` and `events.status`, and `price_min <= price_max`. Defaults: `gen_random_uuid()` for uuid ids, `status 'active'`, `started_at now()`, `updated_at now()`. Delete behaviour: `events.venue_id` uses `set null`, `curated_pages.venue_id` uses `cascade`, and FKs to `cities` use `restrict`. `job_runs.city_id` stays nullable for jobs that span cities. Every other column is nullable, as in the spec.
- Indexes: GIST on `cities.centre_geom`, `areas.centre_geom`, `venues.geom` and `events.geom`. HNSW with `vector_cosine_ops` on both `embedding` columns. BTREE on `events.start_at` and `events.venue_id`. GIN on `areas.aliases`. Unique `(source, source_id)` on venues and events.
- RLS is on for all nine tables. `cities`, `areas` and `venues` are readable by `anon` and `authenticated`. `events` are readable when `status = 'active'` and the event hasn't ended. `curated_pages`, `job_runs`, `usage_daily`, `config` and `schema_migrations` have no policies and no grants. No table has a write policy; only `service_role` (which bypasses RLS) writes.
- `usage_daily.date` is a UTC date, to match provider resets. The column keeps the name `date`.
- Seeds: `db/seed/cities.sql` (London, the only active city) and `db/seed/areas.sql` (33 boroughs including the City of London, and 20 neighbourhoods). Both are idempotent with `on conflict (id) do update`. Area ids are `london-<slug>`. Boroughs get a 3 km default radius and neighbourhoods 1 km. London's settings: centre at Charing Cross, 10 km radius, `holiday_region 'england-and-wales'`, `travel_time_provider 'tfl'`.
- Neighbourhoods: Soho, Shoreditch, Camden Town, South Bank, Covent Garden, King's Cross, Brixton, Peckham, Dalston, Hackney Wick, Notting Hill, Clapham, Mayfair, Marylebone, Fitzrovia, Bermondsey, Borough, Angel, Greenwich town centre and Stratford. Ambiguous names (Camden, Greenwich) are deliberately not aliases; the agent resolves them.
- CI: a `db` job in `ci.yml` runs on the same triggers as the agent job. It creates a fresh database, runs `db/ci/bootstrap.sql`, applies migrations, applies them again (expecting a skip), runs 001 directly (expecting failure), loads the seeds twice and runs `db/tests/rls_and_indexes.sql`.

## Supabase default privileges

The SQL editor on the project shows that Supabase's default privileges give `anon` and `authenticated` full rights on new tables, sequences and functions in `public`, for objects owned by both `postgres` and `supabase_admin`. RLS would be the only barrier, so 001 also:

1. Revokes all privileges from `anon` and `authenticated` on every object it creates, and EXECUTE from `public` on `set_active_config`.
2. Runs `alter default privileges for role postgres … revoke` for tables, sequences and functions, every time.
3. Runs the same revoke for `supabase_admin` only inside a `DO` block that checks the current role is a member of `supabase_admin`, and raises a NOTICE otherwise. Only a member may change another role's default privileges, so an unconditional statement would fail the migration if `postgres` isn't one.
4. Grants `select` on `cities`, `areas`, `venues` and `events` to `anon` and `authenticated`, and all privileges to `service_role`.

`bootstrap.sql` recreates the same defaults in CI for both owners, so CI tests the real starting conditions. CLAUDE.md now requires every later migration to revoke and grant explicitly.

**Role check on Supabase** (`select rolsuper, pg_has_role('postgres', 'supabase_admin', 'member') from pg_roles where rolname = 'postgres'`): **result not yet recorded.** Fill this in before 001 is applied to Supabase. If both values are false, expect the NOTICE, and the `supabase_admin` defaults stay as they are. That leaves them harmless for our objects, because our migrations create objects as `postgres` and revoke explicitly.

## Sources

Centre points are from OpenStreetMap via Nominatim, looked up on 2026-10-26, © OpenStreetMap contributors, available under the Open Database Licence (ODbL). Each seed row names its OSM object. Borough points are the centroids Nominatim returns for the borough boundary relations, not town centres. Neighbourhood points are OSM place nodes (or, for Soho, the place relation). Borough is node/27553712 ("The Borough"). London's centre is the Charles I statue at Charing Cross, node/12877095809, the traditional point distances from London are measured from. The site will carry the OSM attribution wherever these points are used, as it already must for map tiles.

## Trade-offs

- **The `supabase_admin` revoke may not run on Supabase.** Objects Supabase itself creates in `public` could still be granted to `anon`. Our own objects are covered by explicit revokes. CI can't exercise the NOTICE path, because CI's `postgres` is a superuser. That path was checked by hand with a non-member role.
- **The CI database isn't exactly Supabase.** The roles, schemas and default privileges are recreated, but not Supabase's other schemas, `search_path` or role memberships.
- **CI can't detect edits against a fresh database.** The PR diff check covers that, and the runner's checksums cover the real database.
- **The 3-hour fallback is a guess.** It's a literal in the policy, not config, because RLS shouldn't read the private config table.
- **The `coalesce(...)` predicate can't use the `start_at` index.** That's fine at MVP size. Add an expression index if event reads slow down.
- **`embedding` is readable by `anon`.** The frontend should select columns explicitly rather than `*`.

## Metrics and results

- **CI images.** `postgis/postgis:17-3.5` is Debian 11 (bullseye). Its `apt.postgresql.org` bullseye-pgdg repo returns 404, so `postgresql-17-pgvector` can't be installed. `postgis/postgis:17-3.6` is Debian 13 (trixie) with PostgreSQL 17.11 and PostGIS 3.6.4. pgvector 0.8.7 (`0.8.7-1.pgdg13+1`) installs from PGDG (CI run 40311409642).
- **The image preinstalls PostGIS** in `public` in its default database. CI uses a fresh database created from `template1`.
- **Locally, against the same image:** migrations apply with no errors, a second run skips, running 001 again fails with "migration 001 is already applied", the seeds load twice, and every check in `rls_and_indexes.sql` passes.
- **The tests catch breakage.** Each of these deliberate breaks fails the suite:
  - removing the time condition or the 3-hour fallback
  - granting `anon` select on config
  - dropping either default-privilege revoke
  - turning RLS off on one table
  - leaving EXECUTE with `public`
  - dropping the `start_at` index
  - using L2 instead of cosine
  - deleting an area
  - a second active city
  - activating a config version without deactivating the old one

  A missing explicit `service_role` grant isn't caught, because the default privileges cover it on Supabase and in CI.
