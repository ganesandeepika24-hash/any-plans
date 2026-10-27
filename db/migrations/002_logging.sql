-- 002_logging: query logging for the agent (step 1.6, decision 0013).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its checksum.
--
-- The number was reserved for logging (0006, 0008). On Supabase it's applied after 005
-- to 011, out of name order, so migrate.sh prints a notice; a fresh database applies it
-- second. So it depends only on 001 and must not touch anything 005 to 011 create.
-- Profiles, clicks and feedback are deferred to later steps (0013).

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '002') then
    raise exception 'migration 002 is already applied';
  end if;
end $$;

-- One row per agent request (Q-02, columns as proposed in 0013). The agent generates
-- id, so a response can return it before the row is written.
create table public.queries (
  id              uuid primary key,
  created_at      timestamptz not null default now(),
  user_id         uuid not null,  -- the Supabase JWT's sub; no FK, CI has no auth schema
  city_id         text not null references public.cities (id) on delete restrict,
  route           text not null check (route in ('parse')),
  query_text      text not null check (char_length(query_text) between 1 and 500),
  status          text not null check (status in ('ok', 'ai_resting', 'capped')),
  provider        text,           -- the provider that answered; null unless ok
  attempts        jsonb not null default '[]',  -- [{provider, model, outcome}, ...]
  filters         jsonb,          -- search_items' argument; null unless ok
  embedding_text  text,
  notes           jsonb not null default '[]',
  config_versions jsonb not null default '{}',
  release         text,
  trace_id        text check (trace_id ~ '^[0-9a-f]{32}$'),
  latency_ms      integer check (latency_ms >= 0),
  constraint queries_ok_has_filters check ((status = 'ok') = (filters is not null))
);

-- The daily cap counts a user's rows since midnight UTC.
create index queries_user_created_at_idx on public.queries using btree (user_id, created_at);

-- The events a search showed, in order. Decision 0007: source and source_id only, no
-- FK to events and no copied event details, so deleting ended events loses nothing
-- here. /parse shows no events and writes none; the search route will.
create table public.query_results (
  query_id  uuid not null references public.queries (id) on delete cascade,
  rank      integer not null check (rank >= 1),
  source    text not null,
  source_id text not null,
  score     double precision,
  primary key (query_id, rank)
);

-- AI searches a user may run per UTC day (decision 0002). Change it with a new version
-- and set_active_config; the agent rereads config every minute.
insert into public.config (key, version, value_json, updated_by, is_active)
values ('ai_search_cap', 1, '{"per_user_per_day": 10}', 'migration 002', true);

-- Privileges: only the agent's service_role reads or writes these. Revoke the Supabase
-- defaults (decision 0006), grant service_role back, and turn on RLS with no policies.
revoke all on table public.queries, public.query_results from anon, authenticated;
grant all on table public.queries, public.query_results to service_role;
alter table public.queries enable row level security;
alter table public.query_results enable row level security;

insert into public.schema_migrations (version, filename) values ('002', '002_logging.sql');
