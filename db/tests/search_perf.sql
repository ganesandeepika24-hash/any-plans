-- search_items performance (decision 0010): p95 under 500 ms on generated data.
-- Run after migrations and seeds: psql -v ON_ERROR_STOP=1 -f db/tests/search_perf.sql
-- Everything happens in one transaction that's rolled back.
--
-- Data: 20,000 visible events (about twice the expected volume, 0009), plus 1,000 each
-- of cancelled, ended and duplicate events that search must skip, across 500 venues.
-- Points within about 20 km of central London, starts over the next 90 days, half
-- unpriced, 5% without a location and 2% not yet embedded, as after an ingest.

\set ON_ERROR_STOP on
\timing off
begin;

select setseed(0.42);

insert into public.venues (city_id, source, source_id, name, kind, geom)
select 'london', 'perf', 'v' || n, 'Venue ' || n, 'other',
       extensions.st_setsrid(extensions.st_makepoint(
         -0.1278 + (random() - 0.5) * 0.58, 51.5074 + (random() - 0.5) * 0.36), 4326)
  from generate_series(1, 500) n;

create temporary table perf_events on commit drop as
select g,
       case when g <= 20000 then 'active' when g <= 21000 then 'cancelled' else 'active' end
         as status,
       case when g between 21001 and 22000
            then now() - interval '2 days' - random() * interval '20 days'
            else now() + random() * interval '90 days' end as start_at,
       (array['Music', 'Comedy', 'Theatre', 'Sport', 'Nightlife', 'Arts', 'Family',
              'Food & Drink'])[1 + (g % 8)] as category,
       case when random() < 0.5 then null else round((random() * 80)::numeric, 2) end as price,
       random() < 0.05 as no_geom,
       random() < 0.02 as no_embedding,
       random() < 0.1 and g not between 21001 and 22000 as multi_day  -- ended stays ended
  from generate_series(1, 23000) g;

-- Vectors come from a pool of 1,000, which is much faster than one per row and still
-- spreads similarities.
create temporary table perf_vectors on commit drop as
select n, (select array_agg(random() - 0.5) from generate_series(1, 384 + 0 * n))
            ::extensions.vector as embedding
  from generate_series(0, 999) n;

insert into public.events (city_id, source, source_id, title, category, start_at, end_at,
                           venue_id, price_min, price_max, currency, geom, embedding, status)
select 'london', 'perf', 'e' || p.g, 'Event ' || p.g, p.category, p.start_at,
       case when p.multi_day then p.start_at + interval '10 days' end,
       v.id, p.price, p.price + case when p.price is null then null else 10 end,
       case when p.price is null then null else 'GBP' end,
       case when p.no_geom then null else v.geom::extensions.geography end,
       case when p.no_embedding then null else pv.embedding end,
       p.status
  from perf_events p
  join public.venues v on v.source = 'perf' and v.source_id = 'v' || (1 + p.g % 500)
  join perf_vectors pv on pv.n = (p.g * 7919) % 1000;

-- The last 1,000 are duplicates of visible events.
update public.events d
   set duplicate_of = k.id
  from public.events k
 where d.source = 'perf' and substr(d.source_id, 2)::int > 22000
   and k.source = 'perf' and k.source_id = 'e' || (substr(d.source_id, 2)::int - 22000);

analyze public.events;
analyze public.venues;

do $$
declare
  visible integer;
begin
  select count(*) into visible from public.events
   where source = 'perf' and status = 'active' and duplicate_of is null
     and coalesce(end_at, start_at + interval '3 hours') > now();
  if visible <> 20000 then
    raise exception 'expected 20000 visible perf events, generated %', visible;
  end if;
end $$;

-- 100 searches, five shapes in turn. Shape 0 (every visible event scored against an
-- embedding) is the worst case. Three warm-up runs first, not counted.
do $$
declare
  i integer;
  f jsonb;
  q text;
  plan jsonb;
  ms double precision;
  times double precision[] := '{}';
  p50 double precision;
  p95 double precision;
  worst double precision;
  centre_lat double precision;
  centre_lon double precision;
  day_offset integer;
begin
  for i in -2..99 loop
    centre_lat := 51.5074 + (random() - 0.5) * 0.2;
    centre_lon := -0.1278 + (random() - 0.5) * 0.3;
    day_offset := (random() * 60)::int;
    q := (select array_agg(random() - 0.5) from generate_series(1, 384))::extensions.vector::text;
    f := case abs(i) % 5
      when 0 then '{}'::jsonb
      when 1 then jsonb_build_object('lat', centre_lat, 'lon', centre_lon,
                                     'radius_km', 2 + random() * 8)
      when 2 then jsonb_build_object(
                    'date_from', to_char(now() + make_interval(days => day_offset), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'date_to', to_char(now() + make_interval(days => day_offset + 3), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'categories', jsonb_build_array('Music', 'Comedy'))
      when 3 then jsonb_build_object('price_max', 20, 'include_unpriced', false,
                    'lat', centre_lat, 'lon', centre_lon, 'radius_km', 5,
                    'date_from', to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'date_to', to_char(now() + interval '14 days', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))
      else jsonb_build_object('lat', centre_lat, 'lon', centre_lon, 'radius_km', 10,
                    'date_from', to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'date_to', to_char(now() + interval '30 days', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'categories', jsonb_build_array('Music', 'Nightlife', 'Arts'),
                    'price_max', 40, 'limit', 100)
    end;
    if abs(i) % 5 = 3 then
      q := null;  -- the filter panel: no text, no embedding
    end if;

    execute format(
      'explain (analyze, format json) select * from public.search_items(%L::jsonb, %L::extensions.vector)',
      f, q) into plan;
    ms := (plan->0->>'Execution Time')::double precision;
    if i >= 0 then
      times := times || ms;
    end if;
  end loop;

  select percentile_cont(0.5) within group (order by t),
         percentile_cont(0.95) within group (order by t),
         max(t)
    into p50, p95, worst
    from unnest(times) t;
  raise notice 'search_items over 100 searches: p50 % ms, p95 % ms, max % ms',
    round(p50::numeric, 1), round(p95::numeric, 1), round(worst::numeric, 1);
  if p95 >= 500 then
    raise exception 'search_items p95 is % ms, over the 500 ms limit', round(p95::numeric, 1);
  end if;
end $$;

-- The public filter search (migration 023): the same function as the signed-out
-- browser calls it, as anon, with no embedding, windows included (024). Same data,
-- same 500 ms limit.
do $$
declare
  f jsonb;
  plan jsonb;
  times double precision[] := '{}';
  p95 double precision;
begin
  set local role anon;
  for i in -2..99 loop
    f := case abs(i) % 4
      when 0 then '{}'::jsonb
      when 3 then jsonb_build_object(  -- the next 4 Saturdays (024)
                    'date_from', to_char(date_trunc('day', now()) + interval '1 day', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'date_to', to_char(date_trunc('day', now()) + interval '23 days', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'windows', (select jsonb_agg(jsonb_build_array(
                                  to_char(date_trunc('day', now()) + make_interval(days => 1 + 7 * n), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                                  to_char(date_trunc('day', now()) + make_interval(days => 2 + 7 * n), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')))
                                  from generate_series(0, 3) n))
      when 1 then jsonb_build_object('lat', 51.5074 + (random() - 0.5) * 0.2,
                                     'lon', -0.1278 + (random() - 0.5) * 0.3,
                                     'radius_km', 2 + random() * 8,
                                     'categories', jsonb_build_array('Music', 'Comedy'))
      else jsonb_build_object('price_max', 20, 'include_unpriced', false,
                    'date_from', to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'date_to', to_char(now() + interval '14 days', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'limit', 100)
    end;
    execute format('explain (analyze, format json) select * from public.search_items_public(%L::jsonb)', f)
      into plan;
    if i >= 0 then
      times := times || (plan->0->>'Execution Time')::double precision;
    end if;
  end loop;
  reset role;
  select percentile_cont(0.95) within group (order by t) into p95 from unnest(times) t;
  raise notice 'search_items_public over 100 searches as anon: p95 % ms', round(p95::numeric, 1);
  if p95 >= 500 then
    raise exception 'search_items_public p95 is % ms, over the 500 ms limit', round(p95::numeric, 1);
  end if;
end $$;

rollback;

\echo 'search_perf: p95 under 500 ms'
