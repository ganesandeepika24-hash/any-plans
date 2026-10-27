-- 011_understand: config and reference data for the Understand node (step 1.5, decision 0012).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its checksum.
--
-- Depends only on 001 and its seeds' tables. On Supabase it is applied before 002 to 004
-- are written; they must not replace understand_context or the llm_understand and
-- understand_prompt config keys.

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '011') then
    raise exception 'migration 011 is already applied';
  end if;
end $$;

-- Providers in fallback order (decision 0002). Change models or order with a new version
-- and set_active_config: the agent rereads config every minute, so no redeploy is needed.
-- structured: send Groq-style strict JSON schema in response_format. Cloudflare's docs
-- don't say whether its gpt-oss-20b honours one, so it gets the schema in the prompt
-- instead. Either way the agent validates the reply.
insert into public.config (key, version, value_json, updated_by, is_active)
values ('llm_understand', 1, $json$
{
  "providers": [
    {"provider": "groq", "model": "openai/gpt-oss-20b", "structured": true,
     "timeout_s": 8, "max_tokens": 1024, "temperature": 0, "reasoning_effort": "low"},
    {"provider": "cloudflare", "model": "@cf/openai/gpt-oss-20b", "structured": false,
     "timeout_s": 12, "max_tokens": 1024, "temperature": 0, "reasoning_effort": null}
  ]
}
$json$::jsonb, 'migration 011', true);

-- The system prompt. The JSON schema itself comes from the agent's UnderstoodQuery model,
-- so it can't drift from the validator.
insert into public.config (key, version, value_json, updated_by, is_active)
values ('understand_prompt', 1, jsonb_build_object('system', $prompt$You turn a search for things to do in London into JSON. Extract only what the user said. Never work out dates, distances or prices yourself: code does that.

Fields:
- keywords: the activity or topic in the user's words, without dates, places or prices ("jazz", "drag brunch"). null if there's none.
- categories: any of Music, Comedy, Theatre, Sport, Nightlife, Arts, Family, Food & Drink that the search clearly asks for. null if none is clear.
- when: null if no time is given. Otherwise kind is one of today, tonight, tomorrow, this_weekend, next_weekend, this_week, next_week, weekday, date.
  - weekday: a named day ("on Friday"). Set weekday to mon, tue, wed, thu, fri, sat or sun.
  - date: a calendar date ("14 November"). Set month and day as numbers. Never add a year.
  - Leave weekday, month and day null unless kind needs them.
- place: the place name as written ("near Brixton" gives "Brixton"). null if there's none, or if it's just London.
- distance: only if the user gives one ("within 2 miles" gives value 2, unit "mi"). unit is "km" or "mi". null otherwise.
- price_min, price_max: amounts in pounds, only if stated ("under £20" gives price_max 20). null otherwise.
- free: true only if the user asks for free events, otherwise false.

Reply with the JSON object only.$prompt$), 'migration 011', true);

-- The city's time zone and its areas' points, for resolving dates and place names in
-- code (principle 5). PostgREST returns geography as WKB, so this hands back plain
-- coordinates. Read-only (stable); the agent caches the result.
create function public.understand_context(city_id text)
returns jsonb
language sql
stable
set search_path = ''
as $$
  select jsonb_build_object(
    'city', jsonb_build_object('id', c.id, 'name', c.name, 'timezone', c.timezone),
    'areas', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', a.id,
               'name', a.name,
               'kind', a.kind,
               'aliases', coalesce(to_jsonb(a.aliases), '[]'::jsonb),
               'lat', extensions.st_y(a.centre_geom::extensions.geometry),
               'lon', extensions.st_x(a.centre_geom::extensions.geometry),
               'default_radius_km', a.default_radius_km)
             order by a.id)
        from public.areas a
       where a.city_id = c.id and a.centre_geom is not null), '[]'::jsonb))
    from public.cities c
   where c.id = understand_context.city_id;
$$;

-- Only the agent's service_role calls it. Revoke the Supabase defaults (decision 0006)
-- and execute from public, then grant execute back to service_role.
revoke all on function public.understand_context(text) from public, anon, authenticated;
grant execute on function public.understand_context(text) to service_role;

insert into public.schema_migrations (version, filename) values ('011', '011_understand.sql');
