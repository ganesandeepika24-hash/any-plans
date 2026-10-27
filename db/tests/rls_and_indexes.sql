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
                             'price_snapshots'] loop
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
     or (select count(*) from public.areas where kind = 'neighbourhood') <> 20
     or exists (select 1 from public.areas where city_id <> 'london' or centre_geom is null) then
    raise exception 'areas seed is wrong: expected 33 boroughs and 20 neighbourhoods in London, all with centres';
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

  -- 013 makes understand_prompt v2 active and keeps v1 for switching back.
  if (select count(*) from public.config where key = 'llm_understand' and version = 1 and is_active) <> 1
     or (select count(*) from public.config where key = 'understand_prompt' and version = 2 and is_active) <> 1
     or (select count(*) from public.config where key = 'understand_prompt' and version = 1 and not is_active) <> 1 then
    raise exception 'expected llm_understand v1 and understand_prompt v2 active, with prompt v1 kept';
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

rollback;

\echo 'rls_and_indexes: all checks passed'
