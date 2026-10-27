-- 010_search_items: the search function behind the AI search bar and the filter panel
-- (step 1.3, decision 0010).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its checksum.
--
-- Depends only on 001, 007 (events.duplicate_of) and 009 (embeddings are written with
-- hashes). On Supabase it is applied before 002 to 004 are written; they must not
-- replace search_items or the search_weights config key.

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '010') then
    raise exception 'migration 010 is already applied';
  end if;
end $$;

-- Starting weights (decision 0010). Change them with a new version and
-- set_active_config, not by editing this row: no redeploy needed (principle 8).
insert into public.config (key, version, value_json, updated_by, is_active)
values ('search_weights', 1,
        '{"similarity": 0.6, "distance": 0.25, "date": 0.15, "date_scale_days": 7}',
        'migration 010', true);

-- Events only. `filters` is the resolved form of SearchFilters: the agent turns words
-- into absolute times and a point with a radius before calling (principle 5). Unknown
-- keys and bad values raise 22023, so the two sides can't drift apart silently.
--
--   date_from, date_to   ISO 8601 with an offset; keeps events whose span overlaps
--   categories           non-empty array of events.category values
--   price_min, price_max numbers >= 0; keeps events whose price range overlaps
--   include_unpriced     boolean, default true; false drops events with no price
--   lat, lon, radius_km  all three or none; radius in (0, 100]
--   limit                integer 1 to 100, default 20
--
-- Visibility is events_public_read's, written out because service_role bypasses RLS:
-- active, not a duplicate, and not ended (no end time counts as 3 hours). The tests
-- compare the two row for row.
--
-- score = similarity weight * (1 - cosine distance)
--       + distance weight   * (1 - metres / radius)
--       + date weight       * exp(-days from the search start / date_scale_days)
-- A term is 0 when its input is missing: no query embedding, an event not yet
-- embedded, no point, or an event with no location. Ties go to the earlier start,
-- then the lower id.
--
-- stable: Postgres runs a non-volatile function's queries read-only, so it can't write.
create function public.search_items(
  filters jsonb,
  query_embedding extensions.vector default null
)
returns table (
  id uuid,
  source text,
  title text,
  category text,
  genre text,
  start_at timestamptz,
  end_at timestamptz,
  venue_id uuid,
  venue_name text,
  price_min numeric,
  price_max numeric,
  currency text,
  url text,
  image_url text,
  last_checked_at timestamptz,
  distance_m double precision,
  similarity double precision,
  score double precision
)
language plpgsql
stable
set search_path = ''
as $$
#variable_conflict use_column
declare
  allowed constant text[] := array['date_from', 'date_to', 'categories', 'price_min',
    'price_max', 'include_unpriced', 'lat', 'lon', 'radius_km', 'limit'];
  -- Same values as the events category CHECK (001); a test compares them.
  known_categories constant text[] := array['Music', 'Comedy', 'Theatre', 'Sport',
    'Nightlife', 'Arts', 'Family', 'Food & Drink'];
  offset_pattern constant text := '(Z|[+-][0-9]{2}(:?[0-9]{2})?)$';
  f jsonb := coalesce(filters, '{}');
  unknown text[];
  weights jsonb;
  w_similarity double precision;
  w_distance double precision;
  w_date double precision;
  date_scale double precision;
  v_date_from timestamptz;
  v_date_to timestamptz;
  v_categories text[];
  v_price_min numeric;
  v_price_max numeric;
  v_include_unpriced boolean := true;
  v_point extensions.geography;
  v_radius_m double precision;
  v_limit integer := 20;
  v_reference timestamptz;
begin
  -- Filters ---------------------------------------------------------------------
  if jsonb_typeof(f) <> 'object' then
    raise exception 'search_items: filters must be a JSON object' using errcode = '22023';
  end if;

  select array_agg(k order by k) into unknown
    from jsonb_object_keys(f) k where k <> all (allowed);
  if unknown is not null then
    raise exception 'search_items: unknown filter keys %', unknown using errcode = '22023';
  end if;

  if f ? 'date_from' then
    if jsonb_typeof(f->'date_from') <> 'string' or f->>'date_from' !~ offset_pattern then
      raise exception 'search_items: date_from must be an ISO 8601 string with an offset'
        using errcode = '22023';
    end if;
    v_date_from := (f->>'date_from')::timestamptz;
  end if;
  if f ? 'date_to' then
    if jsonb_typeof(f->'date_to') <> 'string' or f->>'date_to' !~ offset_pattern then
      raise exception 'search_items: date_to must be an ISO 8601 string with an offset'
        using errcode = '22023';
    end if;
    v_date_to := (f->>'date_to')::timestamptz;
  end if;
  if v_date_from >= v_date_to then
    raise exception 'search_items: date_from must be before date_to' using errcode = '22023';
  end if;

  if f ? 'categories' then
    if jsonb_typeof(f->'categories') <> 'array' or jsonb_array_length(f->'categories') = 0
       or exists (select 1 from jsonb_array_elements(f->'categories') c
                   where jsonb_typeof(c) <> 'string') then
      raise exception 'search_items: categories must be a non-empty array of strings'
        using errcode = '22023';
    end if;
    select array_agg(c) into v_categories from jsonb_array_elements_text(f->'categories') c;
    if not v_categories <@ known_categories then
      raise exception 'search_items: unknown categories in %', v_categories
        using errcode = '22023';
    end if;
  end if;

  if f ? 'price_min' then
    if jsonb_typeof(f->'price_min') <> 'number' or (f->>'price_min')::numeric < 0 then
      raise exception 'search_items: price_min must be a number >= 0' using errcode = '22023';
    end if;
    v_price_min := (f->>'price_min')::numeric;
  end if;
  if f ? 'price_max' then
    if jsonb_typeof(f->'price_max') <> 'number' or (f->>'price_max')::numeric < 0 then
      raise exception 'search_items: price_max must be a number >= 0' using errcode = '22023';
    end if;
    v_price_max := (f->>'price_max')::numeric;
  end if;
  if v_price_min > v_price_max then
    raise exception 'search_items: price_min must not exceed price_max' using errcode = '22023';
  end if;

  if f ? 'include_unpriced' then
    if jsonb_typeof(f->'include_unpriced') <> 'boolean' then
      raise exception 'search_items: include_unpriced must be a boolean' using errcode = '22023';
    end if;
    v_include_unpriced := (f->>'include_unpriced')::boolean;
  end if;

  if (f ? 'lat') or (f ? 'lon') or (f ? 'radius_km') then
    if not ((f ? 'lat') and (f ? 'lon') and (f ? 'radius_km')) then
      raise exception 'search_items: lat, lon and radius_km go together' using errcode = '22023';
    end if;
    if jsonb_typeof(f->'lat') <> 'number' or jsonb_typeof(f->'lon') <> 'number'
       or jsonb_typeof(f->'radius_km') <> 'number'
       or (f->>'lat')::double precision not between -90 and 90
       or (f->>'lon')::double precision not between -180 and 180
       or (f->>'radius_km')::double precision <= 0
       or (f->>'radius_km')::double precision > 100 then
      raise exception 'search_items: lat, lon or radius_km out of range' using errcode = '22023';
    end if;
    v_point := extensions.st_setsrid(
      extensions.st_makepoint((f->>'lon')::double precision, (f->>'lat')::double precision),
      4326)::extensions.geography;
    v_radius_m := (f->>'radius_km')::double precision * 1000;
  end if;

  if f ? 'limit' then
    if jsonb_typeof(f->'limit') <> 'number' or (f->>'limit') !~ '^[0-9]+$'
       or (f->>'limit')::integer not between 1 and 100 then
      raise exception 'search_items: limit must be an integer from 1 to 100'
        using errcode = '22023';
    end if;
    v_limit := (f->>'limit')::integer;
  end if;

  if query_embedding is not null and extensions.vector_dims(query_embedding) <> 384 then
    raise exception 'search_items: query_embedding must have 384 dimensions, not %',
      extensions.vector_dims(query_embedding) using errcode = '22023';
  end if;

  -- Weights (config, principle 8) ---------------------------------------------------
  select c.value_json into weights
    from public.config c where c.key = 'search_weights' and c.is_active;
  if weights is null then
    raise exception 'search_items: no active search_weights config' using errcode = '22023';
  end if;
  if jsonb_typeof(weights->'similarity') is distinct from 'number'
     or jsonb_typeof(weights->'distance') is distinct from 'number'
     or jsonb_typeof(weights->'date') is distinct from 'number'
     or jsonb_typeof(weights->'date_scale_days') is distinct from 'number'
     or (weights->>'similarity')::double precision < 0
     or (weights->>'distance')::double precision < 0
     or (weights->>'date')::double precision < 0
     or (weights->>'date_scale_days')::double precision <= 0 then
    raise exception 'search_items: search_weights needs similarity, distance and date >= 0 and date_scale_days > 0'
      using errcode = '22023';
  end if;
  w_similarity := (weights->>'similarity')::double precision;
  w_distance := (weights->>'distance')::double precision;
  w_date := (weights->>'date')::double precision;
  date_scale := (weights->>'date_scale_days')::double precision;

  -- Dates count from the search's start, or from now if that's earlier.
  v_reference := greatest(now(), coalesce(v_date_from, now()));

  -- Search ------------------------------------------------------------------------
  return query
  with matched as (
    select e.id, e.source, e.title, e.category, e.genre, e.start_at, e.end_at, e.venue_id,
           v.name as venue_name, e.price_min, e.price_max, e.currency, e.url, e.image_url,
           e.last_checked_at,
           case when v_point is not null and e.geom is not null
                then extensions.st_distance(e.geom, v_point) end as distance_m,
           case when query_embedding is not null and e.embedding is not null
                then 1 - (e.embedding operator(extensions.<=>) query_embedding) end
             as similarity
      from public.events e
      left join public.venues v on v.id = e.venue_id
     -- events_public_read (001, 007)
     where e.status = 'active'
       and e.duplicate_of is null
       and coalesce(e.end_at, e.start_at + interval '3 hours') > now()
       -- filters
       and (v_date_to is null or e.start_at < v_date_to)
       and (v_date_from is null
            or coalesce(e.end_at, e.start_at + interval '3 hours') > v_date_from)
       and (v_categories is null or e.category = any (v_categories))
       and (v_point is null or extensions.st_dwithin(e.geom, v_point, v_radius_m))
       and (case
              when e.price_min is null and e.price_max is null then v_include_unpriced
              else (v_price_max is null or coalesce(e.price_min, e.price_max) <= v_price_max)
               and (v_price_min is null or coalesce(e.price_max, e.price_min) >= v_price_min)
            end)
  )
  select m.id, m.source, m.title, m.category, m.genre, m.start_at, m.end_at, m.venue_id,
         m.venue_name, m.price_min, m.price_max, m.currency, m.url, m.image_url,
         m.last_checked_at, m.distance_m, m.similarity,
         w_similarity * coalesce(m.similarity, 0)
         + w_distance * coalesce(1 - m.distance_m / v_radius_m, 0)
         + w_date * exp(-greatest(extract(epoch from m.start_at - v_reference), 0)
                        / 86400 / date_scale) as score
    from matched m
   order by score desc, m.start_at, m.id
   limit v_limit;
end;
$$;

-- Privileges: only the agent's service_role calls it. Revoke the Supabase defaults
-- (decision 0006) and execute from public, then grant execute back to service_role.
revoke all on function public.search_items(jsonb, extensions.vector) from public, anon, authenticated;
grant execute on function public.search_items(jsonb, extensions.vector) to service_role;

insert into public.schema_migrations (version, filename) values ('010', '010_search_items.sql');
