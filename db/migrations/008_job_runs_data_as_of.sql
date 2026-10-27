-- 008_job_runs_data_as_of: when the data a job run used was current (decision 0009).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its checksum.
--
-- Depends only on job_runs (001). On Supabase it is applied before 002 to 004 are
-- written; they must not add or alter job_runs.data_as_of, and 003's views must not
-- reference it (decision 0008).

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '008') then
    raise exception 'migration 008 is already applied';
  end if;
end $$;

-- The data's own as-of time as its source states it, such as the OSM extract's
-- replication timestamp. Null for jobs whose source gives none.
alter table public.job_runs add column data_as_of timestamptz;

-- No new objects, so no new grants: the column inherits job_runs' privileges,
-- which give anon and authenticated nothing (001).

insert into public.schema_migrations (version, filename) values ('008', '008_job_runs_data_as_of.sql');
