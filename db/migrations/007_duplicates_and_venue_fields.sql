-- 007_duplicates_and_venue_fields: cross-source duplicates (step 1.2b) and venue fields
-- for OpenStreetMap venues (step 1.2c). Decision 0009.
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its checksum.
--
-- Depends only on 001. On Supabase it is applied before 002 to 004 are written,
-- so they must not replace events_public_read or venues_kind_check (decision 0008).

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '007') then
    raise exception 'migration 007 is already applied';
  end if;
end $$;

-- Duplicates ------------------------------------------------------------------

-- Set by the dedupe job on the less complete record of a cross-source pair. When the
-- kept record is deleted, the other becomes visible again.
alter table public.events
  add column duplicate_of uuid references public.events (id) on delete set null;
create index events_duplicate_of_idx on public.events using btree (duplicate_of);

-- Same expression as 001, plus duplicate_of is null. Dropped and created in the same
-- transaction, so there is never a moment without the policy.
drop policy events_public_read on public.events;
create policy events_public_read on public.events
  for select
  to anon, authenticated
  using (
    status = 'active'
    and duplicate_of is null
    and coalesce(end_at, start_at + interval '3 hours') > now()
  );

-- Venue fields ----------------------------------------------------------------

alter table public.venues drop constraint venues_kind_check;
alter table public.venues add constraint venues_kind_check check (kind in (
  'restaurant', 'bar', 'pub', 'theatre', 'comedy_club', 'jazz_club', 'cabaret_venue',
  'music_venue', 'museum', 'gallery', 'market', 'other'));

alter table public.venues add column website_url text;

-- No new objects, so no new grants: the columns inherit their tables' privileges (001).

insert into public.schema_migrations (version, filename) values ('007', '007_duplicates_and_venue_fields.sql');
