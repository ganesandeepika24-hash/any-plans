-- 022_result_label: event coordinates and the confidence label on search_items' rows
-- (step 1.4 fix 2, decision 0022).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its
-- checksum. Apply it before the agent that reads these columns is deployed: the agent
-- requires them, and answers "Search is unavailable" without them.
--
-- Depends on 001, 007, 009 and 010. Replaces search_items: a function's result columns
-- can't change in place, so it is dropped and created again. Filters, visibility and
-- scoring are 010's, unchanged. Creates no tables or sequences. Its grants are stated
-- again, rather than relying on defaults (decision 0006).

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '022') then
    raise exception 'migration 022 is already applied';
  end if;
end $$;

-- The confidence label rule (claude/confidence-label-decision-1.4.md, decision 0022).
-- Sources not listed here are never official. Add a source here when its ingest job
-- is added; change the window or a site's name with a new version and
-- set_active_config, not by editing this row (principle 8).
insert into public.config (key, version, value_json, updated_by, is_active)
values ('confidence_labels', 1,
        '{"fresh_hours": 24,
          "sources": {"ticketmaster": {"site": "Ticketmaster", "kind": "official"},
                      "skiddle": {"site": "Skiddle", "kind": "official"}}}',
        'migration 022', true);

-- 010's function, with four more columns:
--   confidence        checked_today, check_site or web: a stable code for the app
--   confidence_label  the card's text: "Price checked today", "Check availability on
--                     [site]" or "Found on the web today"
--   lat, lon          the event's own point (events.geom, which distance_m measures
--                     from); null when the event has no point
drop function public.search_items(jsonb, extensions.vector);

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
  confidence text,
  confidence_label text,
  distance_m double precision,
  lat double precision,
  lon double precision,
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
  labels jsonb;
  v_fresh interval;
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

  -- Confidence labels (decision 0022, config, principle 8) -----------------------------
  select c.value_json into labels
    from public.config c where c.key = 'confidence_labels' and c.is_active;
  if labels is null then
    raise exception 'search_items: no active confidence_labels config' using errcode = '22023';
  end if;
  if jsonb_typeof(labels->'fresh_hours') is distinct from 'number'
     or (labels->>'fresh_hours')::double precision <= 0
     or jsonb_typeof(labels->'sources') is distinct from 'object'
     or exists (select 1 from jsonb_each(labels->'sources') s
                 where jsonb_typeof(s.value->'site') is distinct from 'string'
                    or coalesce(s.value->>'kind', '') not in ('official', 'web')) then
    raise exception 'search_items: confidence_labels needs fresh_hours > 0 and sources of {site, kind official or web}'
      using errcode = '22023';
  end if;
  v_fresh := make_interval(secs => (labels->>'fresh_hours')::double precision * 3600);

  -- Dates count from the search's start, or from now if that's earlier.
  v_reference := greatest(now(), coalesce(v_date_from, now()));

  -- Search ------------------------------------------------------------------------
  return query
  with matched as (
    select e.id, e.source, e.title, e.category, e.genre, e.start_at, e.end_at, e.venue_id,
           v.name as venue_name, e.price_min, e.price_max, e.currency, e.url, e.image_url,
           e.last_checked_at,
           -- The label comes from the source and the last check only, never the model.
           -- "Price checked today" needs an official source, a check within fresh_hours
           -- and a price; anything else is "Check availability on [site]". An unlisted
           -- source is never official.
           case
             when s.src->>'kind' = 'web' then 'web'
             when s.src->>'kind' = 'official'
                  and e.last_checked_at >= now() - v_fresh
                  and (e.price_min is not null or e.price_max is not null)
               then 'checked_today'
             else 'check_site'
           end as confidence,
           coalesce(s.src->>'site', 'the booking site') as site,
           extensions.st_y(e.geom::extensions.geometry) as lat,
           extensions.st_x(e.geom::extensions.geometry) as lon,
           case when v_point is not null and e.geom is not null
                then extensions.st_distance(e.geom, v_point) end as distance_m,
           case when query_embedding is not null and e.embedding is not null
                then 1 - (e.embedding operator(extensions.<=>) query_embedding) end
             as similarity
      from public.events e
      left join public.venues v on v.id = e.venue_id
      cross join lateral (select labels->'sources'->e.source as src) s
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
         m.last_checked_at, m.confidence,
         case m.confidence
           when 'checked_today' then 'Price checked today'
           when 'web' then 'Found on the web today'
           else 'Check availability on ' || m.site
         end,
         m.distance_m, m.lat, m.lon, m.similarity,
         w_similarity * coalesce(m.similarity, 0)
         + w_distance * coalesce(1 - m.distance_m / v_radius_m, 0)
         + w_date * exp(-greatest(extract(epoch from m.start_at - v_reference), 0)
                        / 86400 / date_scale) as score
    from matched m
   order by score desc, m.start_at, m.id
   limit v_limit;
end;
$$;

-- Privileges: only the agent's service_role calls it (fix 4 changes this). Revoke the
-- Supabase defaults (decision 0006) and execute from public, then grant execute back.
revoke all on function public.search_items(jsonb, extensions.vector) from public, anon, authenticated;
grant execute on function public.search_items(jsonb, extensions.vector) to service_role;

insert into public.schema_migrations (version, filename) values ('022', '022_result_label.sql');
