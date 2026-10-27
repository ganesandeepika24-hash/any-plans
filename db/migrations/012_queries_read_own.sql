-- 012_queries_read_own: signed-in users may read their own queries rows (decision 0013).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its checksum.
--
-- Depends on 002. The /parse smoke test reads the row for its own query with the user's
-- token, through PostgREST, so no service key leaves Render and GitHub (CLAUDE.md).
-- Reading only: writes stay with service_role, and query_results stays private.

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '012') then
    raise exception 'migration 012 is already applied';
  end if;
end $$;

grant select on table public.queries to authenticated;

-- PostgREST puts the verified JWT's claims in request.jwt.claims; Supabase's auth.uid()
-- reads the same setting. Reading it directly works in CI too, which has no auth schema.
-- With no claims (or no sub), nothing matches; a sub that isn't a UUID is an error, which
-- fails closed (Supabase subs are always UUIDs).
create policy queries_read_own on public.queries
  for select
  to authenticated
  using (
    user_id = (
      select (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid
    )
  );

insert into public.schema_migrations (version, filename) values ('012', '012_queries_read_own.sql');
