-- 009_event_embedding_text: event descriptions and the hash of each event's embedded text
-- (step 1.2d, decision 0009).
-- Apply with db/ci/migrate.sh, which runs this file in one transaction and records its checksum.
--
-- Depends only on 001. events.embedding (vector(384)) and its HNSW index already
-- exist there; this adds what the embedding job needs alongside them. On Supabase it
-- is applied before 002 to 004 are written; they must not add or alter these columns.

do $$
begin
  if exists (select 1 from public.schema_migrations where version = '009') then
    raise exception 'migration 009 is already applied';
  end if;
end $$;

-- Plain text from the source, HTML stripped and capped by the ingest job. Null when
-- the source gives none (Skiddle's field isn't documented yet; see 0009).
alter table public.events
  add column description text
    constraint events_description_length_check check (char_length(description) <= 1000);

-- sha256 (hex) of the embedding model's name and the exact text embedded. The job
-- re-embeds a row when the hash of its current text differs. A hash without a vector
-- means nothing, so it's only allowed alongside one; vectors written before this
-- migration have no hash and are re-embedded once.
alter table public.events
  add column embedding_hash text
    constraint events_embedding_hash_check check (
      embedding_hash is null or (embedding_hash ~ '^[0-9a-f]{64}$' and embedding is not null));

-- No new objects, so no new grants. Both columns inherit events' privileges: 001
-- grants anon and authenticated select on the whole table, filtered by
-- events_public_read, so they can read a visible event's description and hash.
-- Neither is sensitive. A column-level revoke can't narrow a table-level grant.

insert into public.schema_migrations (version, filename) values ('009', '009_event_embedding_text.sql');
