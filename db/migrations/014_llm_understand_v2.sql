-- 014_llm_understand_v2: room for Cloudflare's reasoning (step 1.6, decision 0014).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its checksum.
--
-- Depends on 011 (llm_understand v1). Creates no tables, sequences or functions, so
-- there's nothing new to revoke or grant.

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '014') then
    raise exception 'migration 014 is already applied';
  end if;
end $$;

-- In the first eval run, three of Cloudflare's 27 replies were cut off at 1,024 tokens:
-- gpt-oss reasons before it answers, and on Cloudflare that runs at its default (medium)
-- effort. Cloudflare's docs don't say whether its chat completions endpoint takes a
-- reasoning effort, so v2 gives it 2,048 tokens instead. Only generated tokens are
-- charged. Groq is unchanged. Switching back is set_active_config('llm_understand', 1).
insert into public.config (key, version, value_json, updated_by, is_active)
values ('llm_understand', 2, $json$
{
  "providers": [
    {"provider": "groq", "model": "openai/gpt-oss-20b", "structured": true,
     "timeout_s": 8, "max_tokens": 1024, "temperature": 0, "reasoning_effort": "low"},
    {"provider": "cloudflare", "model": "@cf/openai/gpt-oss-20b", "structured": false,
     "timeout_s": 12, "max_tokens": 2048, "temperature": 0, "reasoning_effort": null}
  ]
}
$json$::jsonb, 'migration 014', false);

select public.set_active_config('llm_understand', 2);

insert into public.schema_migrations (version, filename) values ('014', '014_llm_understand_v2.sql');
