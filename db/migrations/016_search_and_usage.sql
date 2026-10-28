-- 016_search_and_usage: the search route's queries rows, and daily provider counters
-- (stage 1 step 1, decision 0016). Apply with db/ci/migrate.sh, which runs this file in one
-- transaction and records its checksum.
--
-- Depends on 001 (usage_daily, config) and 002 (queries, query_results). Creates two
-- functions: each revokes all from public, anon and authenticated and grants execute to
-- service_role only. No tables or sequences.

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '016') then
    raise exception 'migration 016 is already applied';
  end if;
end $$;

-- POST /search writes a queries row like /parse does, and its events to query_results.
alter table public.queries
  drop constraint queries_route_check,
  add constraint queries_route_check check (route in ('parse', 'search'));

-- Daily provider limits (principle 1). The agent stops calling a provider for the rest of
-- the UTC day once either count reaches its limit, and falls back to the next one.
-- Groq: 1,000 requests and 200,000 tokens a day for openai/gpt-oss-20b (rate-limits page,
-- checked 28 Oct 2026). Cloudflare: 10,000 free neurons a day, about 50 a call, so 200
-- calls; tokens aren't capped there. null means no limit. Change with a new version and
-- set_active_config: no redeploy.
insert into public.config (key, version, value_json, updated_by, is_active)
values ('provider_limits', 1, $json$
{
  "groq": {"calls_per_day": 1000, "tokens_per_day": 200000},
  "cloudflare": {"calls_per_day": 200, "tokens_per_day": null}
}
$json$::jsonb, 'migration 016', true);

-- usage_daily keeps counts only (no prompts, replies or users): a provider's calls under
-- its own name, and its tokens under '<provider>_tokens'. daily_limit records the limit in
-- force when the row was last written.

-- Counts one call for p_service on p_day and returns true, or returns false without
-- counting when the call or token limit is already reached. One statement, so two
-- requests at once can't both take the last call.
create function public.reserve_provider_call(
  p_service text,
  p_day date,
  p_call_limit integer,
  p_token_limit integer default null
)
returns boolean
language plpgsql
volatile
set search_path = ''
as $$
declare
  reserved boolean;
begin
  if p_call_limit is null or p_call_limit < 1 then
    return false;
  end if;
  if p_token_limit is not null and coalesce(
       (select u.calls from public.usage_daily u
         where u.date = p_day and u.service = p_service || '_tokens'), 0) >= p_token_limit then
    return false;
  end if;
  insert into public.usage_daily as u (date, service, calls, daily_limit)
  values (p_day, p_service, 1, p_call_limit)
  on conflict (date, service) do update
    set calls = u.calls + 1, daily_limit = excluded.daily_limit
    where u.calls < excluded.daily_limit
  returning true into reserved;
  return coalesce(reserved, false);
end;
$$;

-- Adds the tokens a call used (input plus output, as the provider reports them).
create function public.add_provider_tokens(
  p_service text,
  p_day date,
  p_tokens integer,
  p_token_limit integer default null
)
returns void
language sql
volatile
set search_path = ''
as $$
  insert into public.usage_daily as u (date, service, calls, daily_limit)
  values (p_day, p_service || '_tokens', greatest(p_tokens, 0), p_token_limit)
  on conflict (date, service) do update
    set calls = u.calls + greatest(p_tokens, 0), daily_limit = excluded.daily_limit;
$$;

revoke all on function public.reserve_provider_call(text, date, integer, integer)
  from public, anon, authenticated;
revoke all on function public.add_provider_tokens(text, date, integer, integer)
  from public, anon, authenticated;
grant execute on function public.reserve_provider_call(text, date, integer, integer) to service_role;
grant execute on function public.add_provider_tokens(text, date, integer, integer) to service_role;

insert into public.schema_migrations (version, filename) values ('016', '016_search_and_usage.sql');
