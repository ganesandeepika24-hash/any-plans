-- 023_search_items_public: filter-only search for everyone, through search_items itself
-- (step 1.4 fix 4, decision 0023).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its
-- checksum.
--
-- Depends on 022 (search_items with the label and the event's point). Creates one
-- function and no tables or sequences. Its grants are stated, rather than relying on
-- defaults (decision 0006). search_items and config keep theirs: service_role only.

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '023') then
    raise exception 'migration 023 is already applied';
  end if;
end $$;

-- The filter panel's search, signed in or out (PRD F-01): the browser calls it with the
-- publishable key. It is search_items with no query embedding, so the same filters give
-- the same rows, order, labels and distances as the agent gets for them. It returns the
-- agent's card fields: not venue_id or similarity, and no rank (the order is the rank).
--
-- security definer, because search_items reads its weights and label rules from config,
-- which anon can't read and mustn't (prompts, models, provider limits). The function
-- takes only the filters, which search_items checks key by key, and passes no embedding.
-- Visibility is search_items' own, written out to match events_public_read (010): the
-- owner's rights never show a duplicate, cancelled or ended event. search_path is empty
-- and every name is qualified.
create function public.search_items_public(filters jsonb)
returns table (
  id uuid,
  source text,
  title text,
  category text,
  genre text,
  start_at timestamptz,
  end_at timestamptz,
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
  score double precision
)
language sql
stable
security definer
set search_path = ''
as $$
  select s.id, s.source, s.title, s.category, s.genre, s.start_at, s.end_at,
         s.venue_name, s.price_min, s.price_max, s.currency, s.url, s.image_url,
         s.last_checked_at, s.confidence, s.confidence_label, s.distance_m, s.lat, s.lon,
         s.score
    from public.search_items(filters, null) with ordinality as s
   order by s.ordinality;
$$;

-- Privileges: revoke the Supabase defaults (decision 0006) and execute from public, then
-- grant execute to the two browser roles and the agent's.
revoke all on function public.search_items_public(jsonb) from public, anon, authenticated;
grant execute on function public.search_items_public(jsonb) to anon, authenticated, service_role;

insert into public.schema_migrations (version, filename) values ('023', '023_search_items_public.sql');
