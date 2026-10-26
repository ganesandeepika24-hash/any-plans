-- 001_core: core schema for step 0.5 (decision 0006).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its checksum.

-- Migration tracking ---------------------------------------------------------

create table if not exists public.schema_migrations (
  version    text primary key,
  filename   text not null,
  checksum   text,
  applied_at timestamptz not null default now()
);

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '001') then
    raise exception 'migration 001 is already applied';
  end if;
end $$;

-- Extensions -----------------------------------------------------------------

-- Supabase keeps extensions in their own schema. Types and operators below are
-- schema-qualified so nothing depends on search_path.
create schema if not exists extensions;
create extension if not exists postgis with schema extensions;
create extension if not exists vector with schema extensions;

-- "if not exists" is a no-op when an extension already lives elsewhere, and the
-- qualified types would then fail with a confusing error. Fail clearly instead.
do $$
declare
  misplaced text;
begin
  select string_agg(extname || ' in ' || extnamespace::regnamespace, ', ')
    into misplaced
    from pg_extension
   where extname in ('postgis', 'vector')
     and extnamespace <> 'extensions'::regnamespace;
  if misplaced is not null then
    raise exception 'expected postgis and vector in schema extensions, found %', misplaced;
  end if;
end $$;

-- pg_cron exists on Supabase but not in the CI image.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    begin
      create extension if not exists pg_cron;
    exception when others then
      raise notice 'pg_cron is available but could not be created (%), skipping', sqlerrm;
    end;
  else
    raise notice 'pg_cron is not available, skipping';
  end if;
end $$;

-- Tables ---------------------------------------------------------------------

create table public.cities (
  id                   text primary key,
  name                 text not null,
  country              text,
  timezone             text,
  currency             text,
  centre_geom          extensions.geography(Point, 4326),
  default_radius_km    numeric,
  holiday_region       text,
  travel_time_provider text,
  active               boolean
);

create table public.areas (
  id                text primary key,
  city_id           text not null references public.cities (id) on delete restrict,
  name              text not null,
  kind              text not null check (kind in ('borough', 'neighbourhood')),
  aliases           text[],
  centre_geom       extensions.geography(Point, 4326),
  default_radius_km numeric
);

create table public.venues (
  id                 uuid primary key default gen_random_uuid(),
  city_id            text not null references public.cities (id) on delete restrict,
  source             text not null,
  source_id          text not null,
  name               text not null,
  kind               text not null check (kind in (
                       'restaurant', 'bar', 'theatre', 'comedy_club', 'jazz_club', 'cabaret_venue',
                       'music_venue', 'museum', 'gallery', 'market', 'other')),
  address            text,
  geom               extensions.geography(Point, 4326),
  booking_url        text,
  booking_opens_rule jsonb,
  rating             numeric,  -- always null in the MVP
  embedding          extensions.vector(384),
  constraint venues_source_source_id_key unique (source, source_id)
);

create table public.events (
  id              uuid primary key default gen_random_uuid(),
  city_id         text not null references public.cities (id) on delete restrict,
  source          text not null,
  source_id       text not null,
  title           text not null,
  category        text not null check (category in (
                    'Music', 'Comedy', 'Theatre', 'Sport', 'Nightlife', 'Arts', 'Family', 'Food & Drink')),
  genre           text,
  start_at        timestamptz not null,
  end_at          timestamptz,  -- ingest must set this for multi-day events (see events_public_read)
  venue_id        uuid references public.venues (id) on delete set null,
  price_min       numeric,
  price_max       numeric,
  currency        text,
  url             text,
  image_url       text,
  geom            extensions.geography(Point, 4326),
  embedding       extensions.vector(384),
  last_checked_at timestamptz,
  status          text not null default 'active' check (status in ('active', 'cancelled', 'past')),
  constraint events_source_source_id_key unique (source, source_id),
  constraint events_price_range_check check (price_min <= price_max)
);

create table public.curated_pages (
  id               uuid primary key default gen_random_uuid(),
  venue_id         uuid not null references public.venues (id) on delete cascade,
  url              text,
  terms_checked_at timestamptz,
  permitted        boolean
);

create table public.job_runs (
  id          bigserial primary key,
  job         text,
  city_id     text references public.cities (id) on delete restrict,  -- null for jobs that span cities
  started_at  timestamptz not null default now(),
  duration_ms integer,  -- null until the run finishes, so a crashed run stays visible
  fetched     integer,
  new         integer,
  updated     integer,
  failed      integer
);

create table public.usage_daily (
  date        date,  -- UTC, to match provider resets
  service     text,
  calls       integer,
  daily_limit integer,
  primary key (date, service)
);

create table public.config (
  key        text,
  version    integer,
  value_json jsonb,
  updated_by text,
  updated_at timestamptz not null default now(),
  is_active  boolean,
  primary key (key, version)
);

-- Indexes --------------------------------------------------------------------

create index cities_centre_geom_idx on public.cities using gist (centre_geom);
create index areas_centre_geom_idx on public.areas using gist (centre_geom);
create index areas_aliases_idx on public.areas using gin (aliases);
create index venues_geom_idx on public.venues using gist (geom);
create index venues_embedding_idx on public.venues using hnsw (embedding extensions.vector_cosine_ops);
create index events_geom_idx on public.events using gist (geom);
create index events_embedding_idx on public.events using hnsw (embedding extensions.vector_cosine_ops);
create index events_start_at_idx on public.events using btree (start_at);
create index events_venue_id_idx on public.events using btree (venue_id);

-- One active row per key. Switch versions with set_active_config, not by hand.
create unique index config_one_active_per_key on public.config (key) where is_active;

-- Config versions ------------------------------------------------------------

-- The partial unique index is checked row by row, so a single UPDATE that swaps
-- two rows can fail depending on row order. Deactivate first, then activate.
create function public.set_active_config(key text, version integer)
returns void
language plpgsql
set search_path = ''
as $$
begin
  -- Lock the key's rows so concurrent switches queue instead of racing.
  perform 1 from public.config c where c.key = set_active_config.key for update;

  if not exists (
    select 1 from public.config c
     where c.key = set_active_config.key and c.version = set_active_config.version
  ) then
    raise exception 'config % has no version %', set_active_config.key, set_active_config.version;
  end if;

  update public.config c
     set is_active = false, updated_at = now()
   where c.key = set_active_config.key
     and c.version <> set_active_config.version
     and c.is_active;

  update public.config c
     set is_active = true, updated_at = now()
   where c.key = set_active_config.key
     and c.version = set_active_config.version;
end;
$$;

-- Privileges -----------------------------------------------------------------

-- Supabase grants anon and authenticated full rights on new objects in public.
-- Revoke them on everything this migration creates, then grant back only reads.
revoke all on table
  public.schema_migrations, public.cities, public.areas, public.venues, public.events,
  public.curated_pages, public.job_runs, public.usage_daily, public.config
  from anon, authenticated;
revoke all on sequence public.job_runs_id_seq from anon, authenticated;
revoke all on function public.set_active_config(text, integer) from public, anon, authenticated;

grant select on table public.cities, public.areas, public.venues, public.events to anon, authenticated;

grant all on table
  public.schema_migrations, public.cities, public.areas, public.venues, public.events,
  public.curated_pages, public.job_runs, public.usage_daily, public.config
  to service_role;
grant all on sequence public.job_runs_id_seq to service_role;
grant execute on function public.set_active_config(text, integer) to service_role;

-- Stop the same defaults applying to future objects. Migrations run as postgres.
alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on functions from anon, authenticated;

-- Only a member of supabase_admin may change its defaults, and on Supabase the
-- migration role may not be one. Skip with a notice rather than fail.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'supabase_admin')
     and pg_has_role(current_user, 'supabase_admin', 'member') then
    alter default privileges for role supabase_admin in schema public revoke all on tables from anon, authenticated;
    alter default privileges for role supabase_admin in schema public revoke all on sequences from anon, authenticated;
    alter default privileges for role supabase_admin in schema public revoke all on functions from anon, authenticated;
  else
    raise notice '% is not a member of supabase_admin, so its default privileges are unchanged', current_user;
  end if;
end $$;

-- Row Level Security ---------------------------------------------------------

alter table public.schema_migrations enable row level security;
alter table public.cities enable row level security;
alter table public.areas enable row level security;
alter table public.venues enable row level security;
alter table public.events enable row level security;
alter table public.curated_pages enable row level security;
alter table public.job_runs enable row level security;
alter table public.usage_daily enable row level security;
alter table public.config enable row level security;

create policy cities_public_read on public.cities for select to anon, authenticated using (true);
create policy areas_public_read on public.areas for select to anon, authenticated using (true);
create policy venues_public_read on public.venues for select to anon, authenticated using (true);

-- Events without an end time count as ended three hours after they start.
create policy events_public_read on public.events
  for select
  to anon, authenticated
  using (
    status = 'active'
    and coalesce(end_at, start_at + interval '3 hours') > now()
  );

-- curated_pages, job_runs, usage_daily, config and schema_migrations have no
-- policies: no public access. There are no write policies anywhere; only
-- service_role, which bypasses RLS, can write.

insert into public.schema_migrations (version, filename) values ('001', '001_core.sql');
