-- Checks migration 001 and the seeds. Run as a superuser after db/ci/migrate.sh
-- and both seeds, with ON_ERROR_STOP. Everything runs in one transaction and is
-- rolled back. Each failed check raises an exception that names what broke.

begin;

-- Fixtures, written as service_role. This is also the "service_role can insert" check.
set local role service_role;

insert into public.venues (id, city_id, source, source_id, name, kind)
values ('00000000-0000-0000-0000-000000000001', 'london', 'test', 'venue-1', 'Test venue', 'jazz_club');

insert into public.events (city_id, source, source_id, title, category, start_at, end_at, status, venue_id) values
  ('london', 'test', 'upcoming',       'Upcoming',             'Music',   now() + interval '1 day',  now() + interval '1 day 2 hours', 'active',    '00000000-0000-0000-0000-000000000001'),
  ('london', 'test', 'no-end-started', 'No end, 1 hour in',    'Comedy',  now() - interval '1 hour', null,                            'active',    null),
  ('london', 'test', 'multi-day',      'Exhibition',           'Arts',    now() - interval '10 days', now() + interval '10 days',     'active',    null),
  ('london', 'test', 'no-end-over',    'No end, 4 hours in',   'Comedy',  now() - interval '4 hours', null,                           'active',    null),
  ('london', 'test', 'ended',          'Ended',                'Theatre', now() - interval '3 hours', now() - interval '1 hour',      'active',    null),
  ('london', 'test', 'cancelled',      'Cancelled',            'Music',   now() + interval '1 day',  null,                            'cancelled', null),
  ('london', 'test', 'past',           'Marked past',          'Music',   now() + interval '1 day',  null,                            'past',      null);

insert into public.curated_pages (venue_id, url, permitted) values ('00000000-0000-0000-0000-000000000001', 'https://example.com', true);
insert into public.job_runs (job, city_id) values ('test', 'london');
insert into public.usage_daily (date, service, calls, daily_limit) values (current_date, 'test', 1, 16);
insert into public.config (key, version, value_json, updated_by, is_active) values
  ('test.key', 1, '"one"', 'ci', true),
  ('test.key', 2, '"two"', 'ci', false);

reset role;

-- What anon and authenticated can and can't do.
do $$
declare
  r text;
  t text;
  visible text[];
begin
  foreach r in array array['anon', 'authenticated'] loop
    execute format('set local role %I', r);

    select array_agg(source_id order by source_id) into visible from public.events where source = 'test';
    if visible is distinct from array['multi-day', 'no-end-started', 'upcoming'] then
      raise exception '% sees events %, expected {multi-day,no-end-started,upcoming}', r, visible;
    end if;

    if (select count(*) from public.cities) <> 1
       or (select count(*) from public.areas) = 0
       or (select count(*) from public.venues where source = 'test') <> 1 then
      raise exception '% cannot read cities, areas and venues', r;
    end if;

    foreach t in array array['config', 'usage_daily', 'curated_pages', 'job_runs', 'schema_migrations',
                             'price_snapshots', 'calendar_dates'] loop
      begin
        execute format('select count(*) from public.%I', t);
        raise exception '% can read %', r, t;
      exception when insufficient_privilege then
        null;
      end;
    end loop;

    begin
      insert into public.events (city_id, source, source_id, title, category, start_at)
      values ('london', 'test', 'by-' || r, 'Should fail', 'Music', now());
      raise exception '% can insert into events', r;
    exception when insufficient_privilege then
      null;
    end;

    begin
      update public.cities set name = 'Changed';
      raise exception '% can update cities', r;
    exception when insufficient_privilege then
      null;
    end;

    reset role;

    -- Checked directly: a call would also fail inside the function on config, hiding a missing revoke.
    if has_function_privilege(r, 'public.set_active_config(text, integer)', 'execute') then
      raise exception '% can execute set_active_config', r;
    end if;
  end loop;
end $$;

-- Config: one active row per key, and set_active_config switches it.
do $$
begin
  set local role service_role;

  begin
    insert into public.config (key, version, value_json, is_active) values ('test.key', 3, '"three"', true);
    raise exception 'config allowed two active rows for one key';
  exception when unique_violation then
    null;
  end;

  perform public.set_active_config('test.key', 2);
  if (select array_agg(version) from public.config where key = 'test.key' and is_active) is distinct from array[2] then
    raise exception 'set_active_config did not leave only version 2 active';
  end if;

  begin
    perform public.set_active_config('test.key', 9);
    raise exception 'set_active_config accepted a version that does not exist';
  exception when raise_exception then
    if sqlerrm not like 'config test.key has no version 9' then
      raise;
    end if;
  end;

  reset role;
end $$;

-- RLS is on for every table in public.
do $$
declare
  missing text;
begin
  select string_agg(relname, ', ') into missing
    from pg_class
   where relnamespace = 'public'::regnamespace and relkind = 'r' and not relrowsecurity;
  if missing is not null then
    raise exception 'RLS is off on %', missing;
  end if;
end $$;

-- Indexes and unique constraints.
do $$
declare
  missing text;
begin
  select string_agg(e.idx || ' (' || e.method || ')', ', ') into missing
    from (values
      ('cities',  'cities_centre_geom_idx',      'gist'),
      ('areas',   'areas_centre_geom_idx',       'gist'),
      ('areas',   'areas_aliases_idx',           'gin'),
      ('venues',  'venues_geom_idx',             'gist'),
      ('venues',  'venues_embedding_idx',        'hnsw'),
      ('events',  'events_geom_idx',             'gist'),
      ('events',  'events_embedding_idx',        'hnsw'),
      ('events',  'events_start_at_idx',         'btree'),
      ('events',  'events_venue_id_idx',         'btree'),
      ('config',  'config_one_active_per_key',   'btree')
    ) as e (tbl, idx, method)
   where not exists (
     select 1
       from pg_index i
       join pg_class c on c.oid = i.indexrelid
       join pg_am a on a.oid = c.relam
      where c.relname = e.idx
        and i.indrelid = ('public.' || e.tbl)::regclass
        and a.amname = e.method
   );
  if missing is not null then
    raise exception 'missing indexes: %', missing;
  end if;

  if (select count(*) from pg_index i join pg_class c on c.oid = i.indexrelid
       where c.relname = 'config_one_active_per_key' and i.indisunique
         and pg_get_expr(i.indpred, i.indrelid) = 'is_active') <> 1 then
    raise exception 'config_one_active_per_key is not a unique index on (key) where is_active';
  end if;

  if (select count(*) from pg_constraint
       where contype = 'u'
         and (conrelid, conname) in (('public.venues'::regclass, 'venues_source_source_id_key'),
                                     ('public.events'::regclass, 'events_source_source_id_key'))
         and pg_get_constraintdef(oid) = 'UNIQUE (source, source_id)') <> 2 then
    raise exception 'missing unique (source, source_id) on venues or events';
  end if;

  if (select indexdef from pg_indexes where indexname = 'venues_embedding_idx') not like '%vector_cosine_ops%'
     or (select indexdef from pg_indexes where indexname = 'events_embedding_idx') not like '%vector_cosine_ops%' then
    raise exception 'embedding indexes do not use vector_cosine_ops';
  end if;
end $$;

-- Default privileges: neither owner hands anon or authenticated rights on new objects.
do $$
begin
  if exists (
    select 1
      from pg_default_acl d, aclexplode(d.defaclacl) a
     where d.defaclnamespace = 'public'::regnamespace
       and a.grantee in ('anon'::regrole, 'authenticated'::regrole)
  ) then
    raise exception 'default privileges in public still grant to anon or authenticated';
  end if;

  create table public.ci_probe (x integer);
  if has_table_privilege('anon', 'public.ci_probe', 'select')
     or has_table_privilege('authenticated', 'public.ci_probe', 'insert') then
    raise exception 'a new table in public is open to anon or authenticated';
  end if;
end $$;

-- Seeds and migration tracking.
do $$
begin
  if (select array_agg(id) from public.cities where active) is distinct from array['london'] then
    raise exception 'London is not the only active city';
  end if;

  if (select count(*) from public.areas where kind = 'borough') <> 33
     or (select count(*) from public.areas where kind = 'neighbourhood') <> 1203
     or (select count(*) from public.areas where kind = 'landmark') <> 3
     or exists (select 1 from public.areas where city_id <> 'london' or centre_geom is null) then
    raise exception 'areas seed is wrong: expected 33 boroughs, 1,203 neighbourhoods and 3 landmarks in London (0021), all with centres';
  end if;

  if not exists (select 1 from public.schema_migrations where version = '001' and checksum ~ '^[0-9a-f]{64}$') then
    raise exception 'schema_migrations has no checksum for 001';
  end if;

  if not exists (select 1 from public.schema_migrations where version = '005' and checksum ~ '^[0-9a-f]{64}$') then
    raise exception 'schema_migrations has no checksum for 005';
  end if;

  if not exists (select 1 from public.schema_migrations where version = '006' and checksum ~ '^[0-9a-f]{64}$') then
    raise exception 'schema_migrations has no checksum for 006';
  end if;

  if not exists (select 1 from public.schema_migrations where version = '007' and checksum ~ '^[0-9a-f]{64}$') then
    raise exception 'schema_migrations has no checksum for 007';
  end if;

  if not exists (select 1 from public.schema_migrations where version = '008' and checksum ~ '^[0-9a-f]{64}$') then
    raise exception 'schema_migrations has no checksum for 008';
  end if;

  if not exists (select 1 from public.schema_migrations where version = '009' and checksum ~ '^[0-9a-f]{64}$') then
    raise exception 'schema_migrations has no checksum for 009';
  end if;

  if not exists (select 1 from public.schema_migrations where version = '010' and checksum ~ '^[0-9a-f]{64}$') then
    raise exception 'schema_migrations has no checksum for 010';
  end if;

  if not exists (select 1 from public.schema_migrations where version = '011' and checksum ~ '^[0-9a-f]{64}$') then
    raise exception 'schema_migrations has no checksum for 011';
  end if;

  if not exists (select 1 from public.schema_migrations where version = '002' and checksum ~ '^[0-9a-f]{64}$') then
    raise exception 'schema_migrations has no checksum for 002';
  end if;

  if not exists (select 1 from public.schema_migrations where version = '012' and checksum ~ '^[0-9a-f]{64}$') then
    raise exception 'schema_migrations has no checksum for 012';
  end if;
end $$;

-- Migration 005: job_runs.deleted.
do $$
begin
  if (select data_type from information_schema.columns
       where table_schema = 'public' and table_name = 'job_runs' and column_name = 'deleted')
     is distinct from 'integer' then
    raise exception 'job_runs.deleted is missing or not an integer';
  end if;

  if has_column_privilege('anon', 'public.job_runs', 'deleted', 'select')
     or has_column_privilege('authenticated', 'public.job_runs', 'deleted', 'select') then
    raise exception 'job_runs.deleted is readable by anon or authenticated';
  end if;
end $$;

-- Migration 006: price_snapshots.
do $$
begin
  if (select pg_get_constraintdef(oid) from pg_constraint
       where conrelid = 'public.price_snapshots'::regclass and contype = 'p')
     is distinct from 'PRIMARY KEY (event_id, snapshot_date)' then
    raise exception 'price_snapshots primary key is not (event_id, snapshot_date)';
  end if;

  if not exists (
    select 1 from pg_constraint
     where conrelid = 'public.price_snapshots'::regclass and contype = 'f'
       and confrelid = 'public.events'::regclass and confdeltype = 'c'
  ) then
    raise exception 'price_snapshots.event_id does not cascade from events';
  end if;

  if (select pg_get_constraintdef(oid) from pg_constraint
       where conrelid = 'public.price_snapshots'::regclass and contype = 'c'
         and pg_get_constraintdef(oid) like '%status%')
     is distinct from (select pg_get_constraintdef(oid) from pg_constraint
       where conrelid = 'public.events'::regclass and contype = 'c'
         and pg_get_constraintdef(oid) like '%status%') then
    raise exception 'price_snapshots.status allows different values from events.status';
  end if;

  if has_table_privilege('anon', 'public.price_snapshots', 'insert')
     or has_table_privilege('authenticated', 'public.price_snapshots', 'insert') then
    raise exception 'anon or authenticated can insert into price_snapshots';
  end if;
end $$;

-- Migration 007: duplicates and venue fields.
do $$
begin
  if not exists (
    select 1 from pg_constraint
     where conrelid = 'public.events'::regclass and contype = 'f'
       and confrelid = 'public.events'::regclass and confdeltype = 'n'
       and pg_get_constraintdef(oid) like 'FOREIGN KEY (duplicate_of)%'
  ) then
    raise exception 'events.duplicate_of is missing or not on delete set null';
  end if;

  if not exists (select 1 from pg_indexes where indexname = 'events_duplicate_of_idx') then
    raise exception 'events_duplicate_of_idx is missing';
  end if;

  if (select qual from pg_policies where tablename = 'events' and policyname = 'events_public_read')
     not like '%duplicate_of IS NULL%' then
    raise exception 'events_public_read does not hide duplicates';
  end if;

  if (select count(*) from pg_policies where tablename = 'events') <> 1 then
    raise exception 'events should have exactly one policy';
  end if;

  if (select pg_get_constraintdef(oid) from pg_constraint
       where conrelid = 'public.venues'::regclass and conname = 'venues_kind_check')
     not like '%''pub''%' then
    raise exception 'venues_kind_check does not allow pub';
  end if;

  if (select data_type from information_schema.columns
       where table_schema = 'public' and table_name = 'venues' and column_name = 'website_url')
     is distinct from 'text' then
    raise exception 'venues.website_url is missing or not text';
  end if;
end $$;

-- A pub venue can be inserted, and anon can't see an event marked as a duplicate.
do $$
declare
  kept uuid;
  visible text[];
begin
  insert into public.venues (city_id, source, source_id, name, kind)
  values ('london', 'test', 'pub-1', 'Test pub', 'pub');

  insert into public.events (city_id, source, source_id, title, category, start_at)
  values ('london', 'test-a', 'kept', 'Kept', 'Music', now() + interval '1 day')
  returning id into kept;
  insert into public.events (city_id, source, source_id, title, category, start_at, duplicate_of)
  values ('london', 'test-b', 'dup', 'Duplicate', 'Music', now() + interval '1 day', kept);

  set local role anon;
  select array_agg(source_id order by source_id) into visible
    from public.events where source in ('test-a', 'test-b');
  reset role;
  if visible is distinct from array['kept'] then
    raise exception 'anon sees % instead of only the kept record', visible;
  end if;
end $$;

-- Migration 008: job_runs.data_as_of.
do $$
begin
  if (select data_type from information_schema.columns
       where table_schema = 'public' and table_name = 'job_runs' and column_name = 'data_as_of')
     is distinct from 'timestamp with time zone' then
    raise exception 'job_runs.data_as_of is missing or not timestamptz';
  end if;

  if has_column_privilege('anon', 'public.job_runs', 'data_as_of', 'select')
     or has_column_privilege('authenticated', 'public.job_runs', 'data_as_of', 'select') then
    raise exception 'job_runs.data_as_of is readable by anon or authenticated';
  end if;
end $$;

-- Migration 009: events.description and events.embedding_hash.
do $$
declare
  vec text := '[' || array_to_string(array_fill(0.1::float8, array[384]), ',') || ']';
  hash text := repeat('a', 64);
begin
  if (select string_agg(column_name || ':' || data_type, ',' order by column_name)
        from information_schema.columns
       where table_schema = 'public' and table_name = 'events'
         and column_name in ('description', 'embedding_hash'))
     is distinct from 'description:text,embedding_hash:text' then
    raise exception 'events.description or events.embedding_hash is missing or not text';
  end if;

  -- Accepted: a hash with its vector, and a description at the cap.
  insert into public.events (city_id, source, source_id, title, category, start_at,
                             description, embedding, embedding_hash)
  values ('london', 'test-009', 'ok', 'OK', 'Music', now() + interval '1 day',
          repeat('x', 1000), vec::extensions.vector, hash);

  begin
    insert into public.events (city_id, source, source_id, title, category, start_at, embedding_hash)
    values ('london', 'test-009', 'hash-only', 'Hash only', 'Music', now(), hash);
    raise exception 'events accepted an embedding_hash without an embedding';
  exception when check_violation then null;
  end;

  begin
    insert into public.events (city_id, source, source_id, title, category, start_at,
                               embedding, embedding_hash)
    values ('london', 'test-009', 'bad-hash', 'Bad hash', 'Music', now(),
            vec::extensions.vector, 'not-a-sha256');
    raise exception 'events accepted an embedding_hash that is not 64 hex characters';
  exception when check_violation then null;
  end;

  begin
    insert into public.events (city_id, source, source_id, title, category, start_at, description)
    values ('london', 'test-009', 'long', 'Long', 'Music', now(), repeat('x', 1001));
    raise exception 'events accepted a description over 1000 characters';
  exception when check_violation then null;
  end;

  if has_column_privilege('anon', 'public.events', 'embedding_hash', 'update')
     or has_column_privilege('authenticated', 'public.events', 'description', 'update') then
    raise exception 'anon or authenticated can update events.embedding_hash or description';
  end if;
end $$;

-- Migration 010: search_items and its weights.
do $$
declare
  fn constant regprocedure := 'public.search_items(jsonb, extensions.vector)'::regprocedure;
begin
  if (select provolatile from pg_proc where oid = fn) <> 's' then
    raise exception 'search_items is not stable, so it could write';
  end if;

  if (select prosecdef from pg_proc where oid = fn) then
    raise exception 'search_items must not be security definer';
  end if;

  if (select proconfig from pg_proc where oid = fn) is distinct from array['search_path=""'] then
    raise exception 'search_items must set search_path to empty';
  end if;

  if has_function_privilege('anon', fn, 'execute')
     or has_function_privilege('authenticated', fn, 'execute') then
    raise exception 'anon or authenticated can execute search_items';
  end if;

  if exists (select 1 from pg_proc, aclexplode(proacl) a
              where oid = fn and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
    raise exception 'public can execute search_items';
  end if;

  if not has_function_privilege('service_role', fn, 'execute') then
    raise exception 'service_role cannot execute search_items';
  end if;

  if (select value_json from public.config where key = 'search_weights' and is_active)
     is distinct from '{"similarity": 0.6, "distance": 0.25, "date": 0.15, "date_scale_days": 7}'::jsonb then
    raise exception 'search_weights version 1 is missing, inactive or not the starting weights';
  end if;
end $$;

-- anon can't call search_items even directly.
do $$
begin
  set local role anon;
  begin
    perform public.search_items('{}');
    raise exception 'anon called search_items';
  exception when insufficient_privilege then null;
  end;
  reset role;
end $$;

-- Migration 011: understand_context and the Understand config.
do $$
declare
  fn constant regprocedure := 'public.understand_context(text)'::regprocedure;
begin
  if (select provolatile from pg_proc where oid = fn) <> 's'
     or (select prosecdef from pg_proc where oid = fn)
     or (select proconfig from pg_proc where oid = fn) is distinct from array['search_path=""'] then
    raise exception 'understand_context must be stable, security invoker, with an empty search_path';
  end if;

  if has_function_privilege('anon', fn, 'execute')
     or has_function_privilege('authenticated', fn, 'execute')
     or exists (select 1 from pg_proc, aclexplode(proacl) a
                 where oid = fn and a.grantee = 0 and a.privilege_type = 'EXECUTE')
     or not has_function_privilege('service_role', fn, 'execute') then
    raise exception 'understand_context must be executable by service_role only';
  end if;

  -- 014 makes llm_understand v2 active and 020 understand_prompt v6; earlier versions are
  -- kept for switching back.
  if (select count(*) from public.config where key = 'llm_understand' and version = 2 and is_active) <> 1
     or (select count(*) from public.config where key = 'llm_understand' and version = 1 and not is_active) <> 1
     or (select count(*) from public.config where key = 'understand_prompt' and version = 6 and is_active) <> 1
     or (select count(*) from public.config where key = 'understand_prompt' and version < 6 and not is_active) <> 5 then
    raise exception 'expected llm_understand v2 and understand_prompt v6 active, with earlier versions kept';
  end if;

  if (select value_json->'providers'->0->>'provider' from public.config
       where key = 'llm_understand' and is_active) is distinct from 'groq' then
    raise exception 'llm_understand should try groq first';
  end if;
end $$;

-- Migration 002: queries and query_results.
do $$
declare
  tbl text;
  r text;
  q uuid := gen_random_uuid();
begin
  foreach tbl in array array['public.queries', 'public.query_results'] loop
    if not (select relrowsecurity from pg_class where oid = tbl::regclass) then
      raise exception '% has RLS off', tbl;
    end if;
    foreach r in array array['anon', 'authenticated'] loop
      if has_table_privilege(r, tbl, 'insert')
         or has_table_privilege(r, tbl, 'update') or has_table_privilege(r, tbl, 'delete') then
        raise exception '% can write %', r, tbl;
      end if;
      -- 012 lets authenticated read its own queries rows; nothing else is readable.
      if has_table_privilege(r, tbl, 'select')
         and not (r = 'authenticated' and tbl = 'public.queries') then
        raise exception '% can read %', r, tbl;
      end if;
    end loop;
    if not (has_table_privilege('service_role', tbl, 'select')
            and has_table_privilege('service_role', tbl, 'insert')) then
      raise exception 'service_role cannot read and write %', tbl;
    end if;
  end loop;

  if exists (select 1 from pg_constraint
              where conrelid = 'public.query_results'::regclass and contype = 'f'
                and confrelid = 'public.events'::regclass) then
    raise exception 'query_results must not reference events (decision 0007)';
  end if;

  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.query_results'::regclass and contype = 'f'
                    and confrelid = 'public.queries'::regclass and confdeltype = 'c') then
    raise exception 'query_results.query_id does not cascade from queries';
  end if;

  if (select value_json from public.config where key = 'ai_search_cap' and is_active)
     is distinct from '{"per_user_per_day": 10}'::jsonb then
    raise exception 'ai_search_cap version 1 is missing, inactive or not 10 a day';
  end if;

  -- An ok row needs filters; a resting one has none.
  begin
    insert into public.queries (id, user_id, city_id, route, query_text, status)
    values (gen_random_uuid(), gen_random_uuid(), 'london', 'parse', 'jazz', 'ok');
    raise exception 'queries accepted an ok row without filters';
  exception when check_violation then null;
  end;

  insert into public.queries (id, user_id, city_id, route, query_text, status, filters, trace_id)
  values (q, gen_random_uuid(), 'london', 'parse', 'jazz', 'ok', '{}', repeat('a', 32));
  insert into public.query_results (query_id, rank, source, source_id) values (q, 1, 'skiddle', '1');
  delete from public.queries where id = q;
  if exists (select 1 from public.query_results where query_id = q) then
    raise exception 'deleting a query left its results behind';
  end if;
end $$;

-- Migration 013: needs_clarification rows keep their filters and say what's missing.
do $$
declare
  ok_rows constant text[] := array[
    $r$('ok', '{}', '[]')$r$,
    $r$('needs_clarification', '{}', '["location", "date"]')$r$,
    $r$('ai_resting', null, '[]')$r$,
    $r$('capped', null, '[]')$r$];
  bad_rows constant text[] := array[
    $r$('needs_clarification', '{}', '[]')$r$,
    $r$('needs_clarification', null, '["date"]')$r$,
    $r$('ok', '{}', '["date"]')$r$,
    $r$('ai_resting', null, '["date"]')$r$,
    $r$('needs_clarification', '{}', '["weather"]')$r$,
    $r$('needs_clarification', '{}', '{"date": 1}')$r$,
    $r$('pending', null, '[]')$r$];
  row_sql text;
begin
  foreach row_sql in array ok_rows loop
    execute 'insert into public.queries (id, user_id, city_id, route, query_text, status, filters, missing)'
            ' select gen_random_uuid(), gen_random_uuid(), ''london'', ''parse'', ''q'', s, f::jsonb, m::jsonb'
            ' from (values ' || row_sql || ') v (s, f, m)';
  end loop;
  foreach row_sql in array bad_rows loop
    begin
      execute 'insert into public.queries (id, user_id, city_id, route, query_text, status, filters, missing)'
              ' select gen_random_uuid(), gen_random_uuid(), ''london'', ''parse'', ''q'', s, f::jsonb, m::jsonb'
              ' from (values ' || row_sql || ') v (s, f, m)';
      raise exception 'queries accepted %', row_sql;
    exception when check_violation then null;
    end;
  end loop;
end $$;

-- Migration 015: vibe and who take only the PRD's chips, or null.
do $$
declare
  pair text[];
begin
  foreach pair slice 1 in array array[
      array['Chilled', 'Solo'], array['Lively', 'Date'], array['Cultural', 'Friends'],
      array['Foodie', 'Family with kids'], array['Outdoors', null], array[null, null]] loop
    insert into public.queries (id, user_id, city_id, route, query_text, status, vibe, who)
      values (gen_random_uuid(), gen_random_uuid(), 'london', 'parse', 'q', 'capped', pair[1], pair[2]);
  end loop;
  foreach pair slice 1 in array array[
      array['Romantic', null], array['chilled', null], array[null, 'Couple'], array[null, 'friends']] loop
    begin
      insert into public.queries (id, user_id, city_id, route, query_text, status, vibe, who)
        values (gen_random_uuid(), gen_random_uuid(), 'london', 'parse', 'q', 'capped', pair[1], pair[2]);
      raise exception 'queries accepted vibe % and who %', pair[1], pair[2];
    exception when check_violation then null;
    end;
  end loop;
end $$;

-- Migration 016: search rows, and the daily provider counters.
do $$
declare
  fn regprocedure;
  d constant date := '2030-01-01';
  got boolean[];
begin
  insert into public.queries (id, user_id, city_id, route, query_text, status)
    values (gen_random_uuid(), gen_random_uuid(), 'london', 'search', 'q', 'capped');
  begin
    insert into public.queries (id, user_id, city_id, route, query_text, status)
      values (gen_random_uuid(), gen_random_uuid(), 'london', 'admin', 'q', 'capped');
    raise exception 'queries accepted route admin';
  exception when check_violation then null;
  end;

  foreach fn in array array[
      'public.reserve_provider_call(text, date, integer, integer)'::regprocedure,
      'public.add_provider_tokens(text, date, integer, integer)'::regprocedure] loop
    if has_function_privilege('anon', fn, 'execute')
       or has_function_privilege('authenticated', fn, 'execute')
       or exists (select 1 from pg_proc, aclexplode(proacl) a
                   where oid = fn and a.grantee = 0 and a.privilege_type = 'EXECUTE')
       or not has_function_privilege('service_role', fn, 'execute')
       or (select proconfig from pg_proc where oid = fn) is distinct from array['search_path=""']
       or (select prosecdef from pg_proc where oid = fn) then
      raise exception '% must be security invoker, empty search_path, service_role only', fn;
    end if;
  end loop;

  -- Three calls allowed: the fourth is refused and not counted.
  select array_agg(public.reserve_provider_call('test_llm', d, 3)) into got
    from generate_series(1, 4);
  if got <> array[true, true, true, false]
     or (select calls from public.usage_daily where date = d and service = 'test_llm') <> 3 then
    raise exception 'reserve_provider_call allowed % with calls %', got,
      (select calls from public.usage_daily where date = d and service = 'test_llm');
  end if;
  -- A higher limit later in the day lets calls through again; the next day starts at 0.
  if not public.reserve_provider_call('test_llm', d, 4)
     or not public.reserve_provider_call('test_llm', d + 1, 1)
     or public.reserve_provider_call('test_llm', d + 1, 1) then
    raise exception 'reserve_provider_call ignored a new limit or a new day';
  end if;
  -- Tokens add up, and the token limit stops calls once reached.
  perform public.add_provider_tokens('test_tok', d, 1500, 2000);
  if not public.reserve_provider_call('test_tok', d, 100, 2000) then
    raise exception 'refused under the token limit';
  end if;
  perform public.add_provider_tokens('test_tok', d, 600, 2000);
  if public.reserve_provider_call('test_tok', d, 100, 2000)
     or (select calls from public.usage_daily where date = d and service = 'test_tok_tokens') <> 2100 then
    raise exception 'the token limit didn''t stop calls';
  end if;
  -- No limit, or a zero limit, allows nothing; negative tokens add nothing.
  if public.reserve_provider_call('test_zero', d, 0) or public.reserve_provider_call('test_zero', d, null) then
    raise exception 'reserve_provider_call allowed a call with no call limit';
  end if;
  perform public.add_provider_tokens('test_tok', d, -500);
  if (select calls from public.usage_daily where date = d and service = 'test_tok_tokens') <> 2100 then
    raise exception 'negative tokens changed the count';
  end if;

  if (select value_json from public.config where key = 'provider_limits' and is_active)
     is distinct from '{"groq": {"calls_per_day": 1000, "tokens_per_day": 200000},
                       "cloudflare": {"calls_per_day": 200, "tokens_per_day": null}}'::jsonb then
    raise exception 'provider_limits v1 is missing, inactive or changed';
  end if;
end $$;

-- Migration 018: refine rows say which query they changed; only refine rows do.
do $$
declare
  first_id constant uuid := gen_random_uuid();
  refine_id constant uuid := gen_random_uuid();
begin
  insert into public.queries (id, user_id, city_id, route, query_text, status, filters)
    values (first_id, gen_random_uuid(), 'london', 'search', 'jazz under £40', 'ok', '{}');
  insert into public.queries (id, user_id, city_id, route, query_text, status, filters, refines)
    values (refine_id, gen_random_uuid(), 'london', 'refine', 'cheaper', 'ok', '{}', first_id);
  begin
    insert into public.queries (id, user_id, city_id, route, query_text, status, refines)
      values (gen_random_uuid(), gen_random_uuid(), 'london', 'search', 'q', 'capped', first_id);
    raise exception 'a search row accepted refines';
  exception when check_violation then null;
  end;
  begin
    insert into public.queries (id, user_id, city_id, route, query_text, status, refines)
      values (gen_random_uuid(), gen_random_uuid(), 'london', 'refine', 'q', 'capped', gen_random_uuid());
    raise exception 'refines accepted a query that doesn''t exist';
  exception when foreign_key_violation then null;
  end;
  delete from public.queries where id = first_id;
  if (select refines from public.queries where id = refine_id) is not null then
    raise exception 'deleting a query didn''t clear the refines that pointed at it';
  end if;
  if not exists (select 1 from pg_indexes where indexname = 'queries_refines_idx') then
    raise exception 'queries_refines_idx is missing';
  end if;
  if (select value_json from public.config where key = 'refine_steps' and is_active)
     is distinct from '{"cheaper": 0.5, "closer": 0.3333333333, "min_radius_km": 0.5}'::jsonb
     or not exists (select 1 from public.config where key = 'refine_prompt' and version = 1) then
    raise exception 'refine_steps or refine_prompt v1 is missing or changed';
  end if;
end $$;

-- Migration 021 and the seeds (before 019's block, which empties calendar_dates): landmarks, ONS codes, calendar venue areas, the city centre.
do $$
declare
  ctx jsonb;
begin
  if (select count(*) from public.areas where kind = 'borough' and gss_code ~ '^E09[0-9]{6}$') <> 33
     or (select count(distinct gss_code) from public.areas) <> 33
     or exists (select 1 from public.areas where kind <> 'borough' and gss_code is not null) then
    raise exception 'expected 33 boroughs, each with its own ONS code, and no code elsewhere';
  end if;
  if (select count(*) from public.areas where kind = 'landmark') <> 3
     or (select count(*) from public.areas) <> 1239 then
    raise exception 'the areas seed should load 1,239 areas, 3 of them landmarks';
  end if;
  if (select gss_code from public.areas where id = 'london-westminster') <> 'E09000033' then
    raise exception 'Westminster should be E09000033';
  end if;

  begin
    insert into public.areas (id, city_id, name, kind) values ('test-kind', 'london', 'X', 'street');
    raise exception 'areas accepted an unknown kind';
  exception when check_violation then null;
  end;
  begin
    insert into public.areas (id, city_id, name, kind, gss_code)
      values ('test-gss', 'london', 'X', 'neighbourhood', 'E09000099');
    raise exception 'a neighbourhood accepted an ONS code';
  exception when check_violation then null;
  end;
  begin
    insert into public.areas (id, city_id, name, kind, gss_code)
      values ('test-gss', 'london', 'X', 'borough', 'E06000001');
    raise exception 'a borough accepted a code that isn''t London''s';
  exception when check_violation then null;
  end;
  begin
    insert into public.areas (id, city_id, name, kind, gss_code)
      values ('test-gss', 'london', 'X', 'borough', 'E09000001');
    raise exception 'two areas shared an ONS code';
  exception when unique_violation then null;
  end;

  if (select area_id from public.calendar_dates where id = 'london-2027-chelsea-flower-show')
     is distinct from 'london-kensington-and-chelsea'
     or (select count(*) from public.calendar_dates where area_id is not null) <> 1 then
    raise exception 'only the Chelsea Flower Show should have a venue area in the seed';
  end if;

  ctx := public.understand_context('london');
  if (ctx->'city'->>'default_radius_km')::numeric <> 10
     or round((ctx->'city'->>'lat')::numeric, 6) <> 51.507333
     or round((ctx->'city'->>'lon')::numeric, 6) <> -0.127654 then
    raise exception 'understand_context should return the city centre and radius, got %', ctx->'city';
  end if;
  if jsonb_array_length(ctx->'areas') <> 1239 then
    raise exception 'understand_context returned % areas', jsonb_array_length(ctx->'areas');
  end if;

  -- An area deleted from under a calendar entry leaves the entry without one.
  insert into public.areas (id, city_id, name, kind) values ('test-venue', 'london', 'Venue', 'landmark');
  update public.calendar_dates set area_id = 'test-venue' where id = 'london-2026-hanukkah';
  delete from public.areas where id = 'test-venue';
  if (select area_id from public.calendar_dates where id = 'london-2026-hanukkah') is not null then
    raise exception 'deleting an area didn''t clear calendar_dates.area_id';
  end if;

  if not has_table_privilege('anon', 'public.areas', 'select')
     or has_table_privilege('anon', 'public.areas', 'insert')
     or has_table_privilege('authenticated', 'public.areas', 'update')
     or not has_table_privilege('service_role', 'public.areas', 'insert')
     or has_table_privilege('anon', 'public.calendar_dates', 'select')
     or has_table_privilege('service_role', 'public.calendar_dates', 'insert') then
    raise exception 'areas should be readable by all and writable by service_role only; calendar_dates unchanged';
  end if;
end $$;

-- Migration 019 and the calendar seed: calendar_dates, and understand_context's calendar.
do $$
declare
  ctx jsonb;
  ids text[];
begin
  if not (select relrowsecurity from pg_class where oid = 'public.calendar_dates'::regclass) then
    raise exception 'calendar_dates has no row level security';
  end if;
  if not has_table_privilege('service_role', 'public.calendar_dates', 'select')
     or has_table_privilege('service_role', 'public.calendar_dates', 'insert')
     or has_table_privilege('anon', 'public.calendar_dates', 'select')
     or has_table_privilege('authenticated', 'public.calendar_dates', 'select') then
    raise exception 'calendar_dates should be readable by service_role only';
  end if;
  if (select count(*) from public.calendar_dates where city_id = 'london') <> 12
     or exists (select 1 from public.calendar_dates where source = '' or checked_on > current_date) then
    raise exception 'the calendar seed should load 12 London rows, each with a source and a past check date';
  end if;
  if (select count(*) from public.calendar_dates where estimate) <> 1 then
    raise exception 'only Eid al-Fitr is an estimate in the seed';
  end if;

  begin
    insert into public.calendar_dates (id, city_id, name, kind, starts_on, ends_on, source, checked_on)
      values ('test-backwards', 'london', 'Backwards', 'event', current_date, current_date - 1, 'ci', current_date);
    raise exception 'calendar_dates accepted an end before its start';
  exception when check_violation then null;
  end;
  begin
    insert into public.calendar_dates (id, city_id, name, kind, starts_on, ends_on, source, checked_on)
      values ('test-kind', 'london', 'Kind', 'party', current_date, current_date, 'ci', current_date);
    raise exception 'calendar_dates accepted an unknown kind';
  exception when check_violation then null;
  end;

  -- understand_context leaves out what ended before yesterday (the agent's day runs to
  -- 05:00), keeps the rest, and returns each entry's fields.
  delete from public.calendar_dates;
  insert into public.calendar_dates (id, city_id, name, aliases, kind, starts_on, ends_on, estimate, source, checked_on) values
    ('test-over',      'london', 'Over',      '{}',       'event', current_date - 5, current_date - 2, false, 'ci', current_date),
    ('test-yesterday', 'london', 'Yesterday', '{}',       'event', current_date - 1, current_date - 1, false, 'ci', current_date),
    ('test-coming',    'london', 'Coming',    '{Soon}',   'festival', current_date + 3, current_date + 4, true, 'ci', current_date);
  ctx := public.understand_context('london');  -- who may call it is 011's check
  select array_agg(e->>'id' order by e->>'id') into ids from jsonb_array_elements(ctx->'calendar') e;
  if ids is distinct from array['test-coming', 'test-yesterday'] then
    raise exception 'understand_context returned calendar %', ids;
  end if;
  if (select e from jsonb_array_elements(ctx->'calendar') e where e->>'id' = 'test-coming')
     is distinct from jsonb_build_object('id', 'test-coming', 'name', 'Coming', 'aliases', '["Soon"]'::jsonb,
                                         'starts_on', current_date + 3, 'ends_on', current_date + 4, 'estimate', true,
                                         'area_id', null) then
    raise exception 'understand_context calendar entry has the wrong shape';
  end if;
  if jsonb_array_length(ctx->'areas') = 0 then
    raise exception 'understand_context lost its areas';
  end if;

  -- 019 added refine_prompt v2; 020 makes v3 active (its own check, below).
  if not exists (select 1 from public.config where key = 'refine_prompt' and version = 2) then
    raise exception 'refine_prompt v2 is missing';
  end if;
end $$;

-- Migration 020: date_ranges v1, understand_prompt v6 and refine_prompt v3. Config only.
do $$
begin
  if (select value_json from public.config where key = 'date_ranges' and version = 1 and is_active)
     is distinct from '{"seasons": {"winter": {"start_month": 11, "start_day": 1, "end_month": 1, "end_day": 31}},
                        "max_range_days": 62, "choice_count": 4, "max_count": 8}'::jsonb then
    raise exception 'date_ranges v1 is missing, inactive or changed';
  end if;
  if (select array_agg(version order by version) from public.config where key = 'refine_prompt')
       is distinct from array[1, 2, 3]
     or not exists (select 1 from public.config where key = 'refine_prompt' and version = 3 and is_active) then
    raise exception 'expected refine_prompt v3 active, with v1 and v2 kept';
  end if;
  if not exists (select 1 from public.config
                  where key = 'understand_prompt' and version = 6 and is_active
                    and value_json->>'system' like '%every_weekday%') then
    raise exception 'understand_prompt v6 is not active or lacks the recurring kinds';
  end if;
end $$;

-- Migration 012: authenticated reads only its own queries rows.
do $$
declare
  mine constant uuid := '0f8fad5b-d9cb-469f-a165-70867728950e';
  seen text[];
begin
  if (select count(*) from pg_policies where tablename = 'queries') <> 1
     or (select cmd from pg_policies where tablename = 'queries') <> 'SELECT' then
    raise exception 'queries should have exactly one policy, for select';
  end if;

  insert into public.queries (id, user_id, city_id, route, query_text, status) values
    (gen_random_uuid(), mine, 'london', 'parse', 'mine', 'capped'),
    (gen_random_uuid(), gen_random_uuid(), 'london', 'parse', 'theirs', 'capped');

  perform set_config('request.jwt.claims', json_build_object('sub', mine)::text, true);
  set local role authenticated;
  select array_agg(query_text) into seen from public.queries where query_text in ('mine', 'theirs');
  reset role;
  if seen is distinct from array['mine'] then
    raise exception 'authenticated sees % instead of only its own row', seen;
  end if;

  perform set_config('request.jwt.claims', '', true);
  set local role authenticated;
  select array_agg(query_text) into seen from public.queries where query_text in ('mine', 'theirs');
  reset role;
  if seen is not null then
    raise exception 'authenticated with no claims sees %', seen;
  end if;
end $$;

-- Migration 022: search_items' coordinates and confidence label (decision 0022). The
-- 010 block above checks the function's volatility, search_path and grants; 022 created
-- it again, so they cover 022's version.
do $$
declare
  fn constant regprocedure := 'public.search_items(jsonb, extensions.vector)'::regprocedure;
  labels jsonb;
begin
  if (select array_agg(n order by n) from unnest((select proargnames from pg_proc where oid = fn)) n
       where n in ('lat', 'lon', 'confidence', 'confidence_label'))
     is distinct from array['confidence', 'confidence_label', 'lat', 'lon'] then
    raise exception 'search_items does not return lat, lon, confidence and confidence_label';
  end if;

  select value_json into labels from public.config where key = 'confidence_labels' and is_active;
  if labels is distinct from
     '{"fresh_hours": 24,
       "sources": {"ticketmaster": {"site": "Ticketmaster", "kind": "official"},
                   "skiddle": {"site": "Skiddle", "kind": "official"}}}'::jsonb then
    raise exception 'confidence_labels version 1 is missing, inactive or not 022''s rule';
  end if;
end $$;

-- Migration 023: the public filter search (decision 0023). search_items and config stay
-- closed to the browser roles: the 010 block checks search_items' grants.
do $$
declare
  fn constant regprocedure := 'public.search_items_public(jsonb)'::regprocedure;
begin
  if (select provolatile from pg_proc where oid = fn) <> 's' then
    raise exception 'search_items_public is not stable, so it could write';
  end if;

  if not (select prosecdef from pg_proc where oid = fn) then
    raise exception 'search_items_public must be security definer, to read config for anon';
  end if;

  if (select proconfig from pg_proc where oid = fn) is distinct from array['search_path=""'] then
    raise exception 'search_items_public must set search_path to empty';
  end if;

  if (select pg_get_userbyid(proowner) from pg_proc where oid = fn) <> 'postgres' then
    raise exception 'search_items_public must be owned by postgres, which can read config';
  end if;

  if not has_function_privilege('anon', fn, 'execute')
     or not has_function_privilege('authenticated', fn, 'execute')
     or not has_function_privilege('service_role', fn, 'execute') then
    raise exception 'anon, authenticated and service_role must all execute search_items_public';
  end if;

  if exists (select 1 from pg_proc, aclexplode(proacl) a
              where oid = fn and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
    raise exception 'public can execute search_items_public';
  end if;

  if has_table_privilege('anon', 'public.config', 'select')
     or has_table_privilege('authenticated', 'public.config', 'select') then
    raise exception 'anon or authenticated can read config';
  end if;
end $$;

-- anon gets rows through it, as the signed-out browser does.
do $$
begin
  set local role anon;
  perform * from public.search_items_public('{}');
  reset role;
end $$;

rollback;

\echo 'rls_and_indexes: all checks passed'
