-- 025_ticketmaster_compliance: takedowns, retention in config, and which sources may run
-- on a schedule (decision 0001 update, 29 Oct 2026; PR A of the Ticketmaster work).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its
-- checksum.
--
-- Depends on 001 (events, config) and 006 (price_snapshots). Creates one table, one
-- trigger function and one trigger. No sequences. Each object's grants are stated,
-- rather than relying on defaults (decision 0006).

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '025') then
    raise exception 'migration 025 is already applied';
  end if;
end $$;

-- Takedowns (0001: remove content within 24 hours of an owner's request). One row per
-- removed event, kept so the event can't come back: the trigger below skips it on any
-- later ingest. `reference` points at the request (a ticket or email subject); the
-- requester's details stay with the request, not here.
create table public.takedowns (
  source       text not null,
  source_id    text not null,
  reference    text not null check (length(reference) between 1 and 200),
  requested_at timestamptz not null,
  removed_at   timestamptz not null default now(),
  primary key (source, source_id)
);

alter table public.takedowns enable row level security;
revoke all on table public.takedowns from anon, authenticated;
grant all on table public.takedowns to service_role;

-- An insert or update of a taken-down event is skipped, so a manual ingest can't bring it
-- back. Returning null drops the row silently; the ingest counts it as not new.
create function public.skip_taken_down_events()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if exists (select 1 from public.takedowns t
              where t.source = new.source and t.source_id = new.source_id) then
    return null;
  end if;
  return new;
end;
$$;

revoke all on function public.skip_taken_down_events() from public, anon, authenticated;
grant execute on function public.skip_taken_down_events() to service_role;

create trigger events_skip_taken_down
  before insert or update on public.events
  for each row execute function public.skip_taken_down_events();

-- Retention (0001, 0007): days after an event's end before the nightly delete removes it,
-- per source. 0 is decision 0007's rule, delete at the end, which the 04:00 run applies
-- within a day. Ticketmaster is 0, the owner's choice (a), 29 Oct 2026. A source not
-- listed uses default_days_after_end. Change it with a new version and
-- set_active_config (principle 8), never past what 0001 allows.
insert into public.config (key, version, value_json, updated_by, is_active)
values ('event_retention', 1,
        '{"default_days_after_end": 0, "sources": {"ticketmaster": 0}}',
        'migration 025', true);

-- Which sources may ingest on a schedule (0001). A scheduled run of a source that isn't
-- true here stops before any API call. Skiddle: "Skiddle schedule: APPROVED". Ticketmaster:
-- NOT CLEARED. A test keeps this in step with 0001.
insert into public.config (key, version, value_json, updated_by, is_active)
values ('ingest_schedule', 1, '{"skiddle": true, "ticketmaster": false}',
        'migration 025', true);

insert into public.schema_migrations (version, filename) values ('025', '025_ticketmaster_compliance.sql');
