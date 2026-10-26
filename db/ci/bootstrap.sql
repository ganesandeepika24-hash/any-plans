-- CI only, not a migration. Recreates the Supabase roles, schemas and default
-- privileges that migrations meet on a real project, so CI tests the same
-- conditions. Run as a superuser on a fresh database before db/ci/migrate.sh.

create role anon nologin noinherit;
create role authenticated nologin noinherit;
create role service_role nologin noinherit bypassrls;
create role supabase_admin nologin;

grant usage on schema public to anon, authenticated, service_role;

create schema extensions;
grant usage on schema extensions to anon, authenticated, service_role;

-- Supabase's defaults: full rights on new tables, sequences and functions in
-- public, for objects created by either postgres or supabase_admin.
alter default privileges for role postgres in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges for role postgres in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges for role postgres in schema public grant all on functions to anon, authenticated, service_role;
alter default privileges for role supabase_admin in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges for role supabase_admin in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges for role supabase_admin in schema public grant all on functions to anon, authenticated, service_role;
