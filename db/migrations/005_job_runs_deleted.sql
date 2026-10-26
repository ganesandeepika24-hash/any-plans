-- 005_job_runs_deleted: rows deleted by a job run (decisions 0007 and 0008).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its checksum.
--
-- On Supabase this is applied before 002 to 004 (logging, stage 3 metrics views,
-- watches). It must not depend on them, and they must not create or alter
-- job_runs.deleted. A fresh database applies 001 to 005 in name order, so CI
-- fails if 003 references job_runs.deleted before this migration exists.

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '005') then
    raise exception 'migration 005 is already applied';
  end if;
end $$;

-- Null on rows written before this migration and on jobs that delete nothing.
alter table public.job_runs add column deleted integer;

-- No new objects, so no new grants: the column inherits job_runs' privileges,
-- which give anon and authenticated nothing (001).

insert into public.schema_migrations (version, filename) values ('005', '005_job_runs_deleted.sql');
