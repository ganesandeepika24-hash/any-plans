-- 006_price_snapshots: one price row per event per day (decision 0009).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its checksum.
--
-- Depends only on events (001). On Supabase it is applied before 002 to 004 are
-- written, so they run after it there; a fresh database applies them in name order.
-- 003's views must not reference price_snapshots (decision 0008).

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '006') then
    raise exception 'migration 006 is already applied';
  end if;
end $$;

-- Deleting an event deletes its snapshots (decision 0007: no archive).
create table public.price_snapshots (
  event_id      uuid not null references public.events (id) on delete cascade,
  snapshot_date date not null,  -- the city's local date, computed in Python
  price_min     numeric,
  price_max     numeric,
  currency      text,
  status        text not null check (status in ('active', 'cancelled', 'past')),  -- as events.status (001)
  captured_at   timestamptz not null default now(),
  primary key (event_id, snapshot_date),
  constraint price_snapshots_price_range_check check (price_min <= price_max)
);

-- Revoke Supabase's default grants, then grant only what's needed (CLAUDE.md).
revoke all on table public.price_snapshots from anon, authenticated;
grant all on table public.price_snapshots to service_role;

-- No policies: no public access.
alter table public.price_snapshots enable row level security;

insert into public.schema_migrations (version, filename) values ('006', '006_price_snapshots.sql');
