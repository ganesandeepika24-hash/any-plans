-- 021_london_areas: landmarks and ONS codes on areas, a venue area on calendar entries,
-- and the city's centre in understand_context (stage 1 step 5, decision 0021).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its
-- checksum. Then load db/seed/areas.sql and db/seed/calendar.sql again, in that order.
--
-- Depends on 001 (cities, areas), 011 and 019 (understand_context, calendar_dates). Adds
-- columns and replaces one function. Creates no tables or sequences. Each object it
-- touches has its grants stated again, rather than relying on defaults (decision 0006).

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '021') then
    raise exception 'migration 021 is already applied';
  end if;
end $$;

-- Landmarks ("Regent Street", "Liverpool Street") are areas of their own kind, and
-- boroughs carry their ONS code (E09...). Only boroughs have one.
alter table public.areas
  drop constraint areas_kind_check,
  add constraint areas_kind_check check (kind in ('borough', 'neighbourhood', 'landmark')),
  add column gss_code text unique,
  add constraint areas_gss_code_check check (
    gss_code is null or (kind = 'borough' and gss_code ~ '^E09[0-9]{6}$'));

-- A calendar entry's venue area: "Chelsea Flower Show" searches Kensington and Chelsea
-- when the person names no place. Deleting the area leaves the entry without one.
alter table public.calendar_dates
  add column area_id text references public.areas (id) on delete set null;

-- understand_context, as in 019, plus the city's centre and default radius (the default
-- area, PRD F-04 and M-02) and each calendar entry's area_id.
create or replace function public.understand_context(city_id text)
returns jsonb
language sql
stable
set search_path = ''
as $$
  select jsonb_build_object(
    'city', jsonb_build_object(
              'id', c.id,
              'name', c.name,
              'timezone', c.timezone,
              'lat', extensions.st_y(c.centre_geom::extensions.geometry),
              'lon', extensions.st_x(c.centre_geom::extensions.geometry),
              'default_radius_km', c.default_radius_km),
    'areas', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', a.id,
               'name', a.name,
               'kind', a.kind,
               'aliases', coalesce(to_jsonb(a.aliases), '[]'::jsonb),
               'lat', extensions.st_y(a.centre_geom::extensions.geometry),
               'lon', extensions.st_x(a.centre_geom::extensions.geometry),
               'default_radius_km', a.default_radius_km)
             order by a.id)
        from public.areas a
       where a.city_id = c.id and a.centre_geom is not null), '[]'::jsonb),
    'calendar', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', d.id,
               'name', d.name,
               'aliases', to_jsonb(d.aliases),
               'starts_on', d.starts_on,
               'ends_on', d.ends_on,
               'estimate', d.estimate,
               'area_id', d.area_id)
             order by d.starts_on, d.id)
        from public.calendar_dates d
       where d.city_id = c.id and d.ends_on >= (now() at time zone c.timezone)::date - 1), '[]'::jsonb))
    from public.cities c
   where c.id = understand_context.city_id;
$$;

-- Grants, stated again for each object touched. areas: anyone may read (001's
-- areas_public_read policy), only service_role writes. calendar_dates: only the agent
-- reads (019). understand_context: service_role only (011).
revoke all on table public.areas from anon, authenticated;
grant select on table public.areas to anon, authenticated;
grant all on table public.areas to service_role;

revoke all on table public.calendar_dates from anon, authenticated, service_role;
grant select on table public.calendar_dates to service_role;

revoke all on function public.understand_context(text) from public, anon, authenticated;
grant execute on function public.understand_context(text) to service_role;

insert into public.schema_migrations (version, filename) values ('021', '021_london_areas.sql');
