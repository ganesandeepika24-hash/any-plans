# 0012: The Understand node (step 1.5, migration 011)

Status: accepted (2026-10-27). Owner: deepika. Revisit: after the first live test call per model (0002), when live eval scoring starts, or if either provider changes its structured-output support.

## Context

The AI search bar has to turn "jazz in Soho tonight under £20" into the filters `search_items` accepts (0010). Decision 0002 chose the providers: Groq `gpt-oss-20b` first and Cloudflare Workers AI second, with model names and order in config. Principle 5 splits the work: the model extracts intent, and code resolves dates, places and prices. Principle 8 keeps prompts and models in config, editable without a redeploy. Principle 7 says to log every call.

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| What the model returns | `SearchFilters` directly; **an intent model, `UnderstoodQuery`, that code turns into `SearchFilters`** | Intent model | `SearchFilters` holds absolute times and coordinates. A model filling it in would be computing them, which principle 5 gives to code. |
| A place or date code can't resolve | Treat it as a model failure and fall back; **drop that filter and add a note** | Note | It isn't the model's fault: "Narnia" or "30 February" would fail with any model. The UI can say what it ignored (principle 6). |
| Langfuse client | Official SDK; **plain HTTP** | Plain HTTP | No new dependency on a 512 MB host. The current Langfuse API has no batch ingestion endpoint. Its documented write path is OTLP/HTTP, so the agent posts OTLP JSON itself. |
| Reading config and areas | Direct Postgres; **PostgREST with the service key** | PostgREST | The agent already talks to Supabase over HTTPS, and Render has the service key (CLAUDE.md). PostgREST returns `geography` as WKB, so migration 011 adds `understand_context()`, which returns plain coordinates. |
| Cloudflare structured output | Send the JSON schema; **put the schema in the prompt** | Prompt | Cloudflare's model and compatibility pages don't say whether gpt-oss-20b honours `response_format` (read 27 Oct 2026). Every reply is validated either way. |

## Decision

**The two models** (`agent/app/understand/schema.py`)
- `UnderstoodQuery` is what the model may return. It's strict: `extra="forbid"`, strict types, and every field required with null for "not said", as Groq's strict mode needs.
  - `keywords`, `categories` (the eight values), `place` (as written) and `free`.
  - `when`: one of `today`, `tonight`, `tomorrow`, `this_weekend`, `next_weekend`, `this_week`, `next_week`, `weekday` (with `weekday`) or `date` (with `month` and `day`, never a year).
  - `distance`: a value and a unit, `km` or `mi`.
  - `price_min` and `price_max`.
- `SearchFilters` is `search_items`' ten keys with the same rules. A test reads migration 010's `allowed` and `known_categories` arrays and fails if they differ. Only code builds it.

**Resolution in code** (`resolve.py`), in the city's time zone:
- **Day boundaries:** days run 05:00 to 05:00, so before 05:00 is still the previous day. Evenings start at 17:00.
- **Windows:**
  - `today`: now to 05:00
  - `tonight`: the later of 17:00 and now, to 05:00
  - `tomorrow`: the next 05:00 to 05:00 day
  - `this_weekend`: Friday 17:00 to Monday 05:00, the weekend containing now or the next one. `next_weekend` is the one after that.
  - `this_week`: now to Monday 05:00. `next_week`: the following Monday to Monday.
  - `weekday`: its next occurrence, including today.
  - `date`: its next occurrence within a year. 29 February resolves only when a leap year falls within that year.
- **Every window starts no earlier than now.** Times are local wall times, so a weekend across a clock change still ends at 05:00 local.
- **Places:** matched on area names and aliases from `understand_context`, ignoring case, punctuation, "&" against "and", and a leading "the". There's no partial matching: "Green" isn't Greenwich.
  - The point is the area's centre. The radius is the area's default, or the user's distance, converted from miles in code and capped at 100 km.
  - "London" itself means no location filter.
- **Prices:** `free` means `price_max` 0 and `include_unpriced` false. Other prices pass through.
- **Notes:** an unknown place, an impossible date, a distance without a place, a capped radius or an inverted price range drops that filter and adds a note (`unknown_place`, `no_such_date`, …).

**Calling the models** (`node.py`, `llm.py`)
- **Config** (migration 011, both reread every 60 s):
  - `llm_understand` v1: Groq `openai/gpt-oss-20b` (strict schema, `reasoning_effort: low`, 8 s), then Cloudflare `@cf/openai/gpt-oss-20b` (schema in the prompt, 12 s). Both have `max_tokens` 1024 and `temperature` 0.
  - `understand_prompt` v1: the system prompt.
- **Keys** come only from env: `GROQ_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` and `SUPABASE_SERVICE_KEY`. A missing key is a `no_key` outcome, so the chain falls back.
- **Strict validation:** a reply counts only if it's a JSON object that validates as an `UnderstoodQuery`. One surrounding Markdown code fence is unwrapped, and any other text makes the reply invalid.
- **Fallback:** any failure moves on to the next provider: a timeout, network error, 429, other 4xx or 5xx, an empty reply, a reply cut off at `max_tokens`, bad JSON or a schema failure. Nothing is repaired or guessed.
- **When everything fails:** the result is `ai_resting` with "AI search is resting; filters still work", and the caller falls back to filter-only search (0002). Bad or missing config also rests, without calling any model.
- **Queries** must be 1 to 500 characters.

**Tracing** (`tracing.py`)
- **Transport:** `POST {LANGFUSE_HOST}/api/public/otel/v1/traces`, with Basic auth and `x-langfuse-ingestion-version: 4`, sent as OTLP JSON.
- **Spans:** one trace per query, with a root span holding the input, the output and the config versions as `langfuse.trace.metadata.*`. One generation per attempt records:
  - the model, provider and outcome
  - the token counts, in `langfuse.observation.usage_details`
  - the reply text, including failed replies
  - the config versions
- **Off without keys:** with no Langfuse keys, the tracer does nothing.
- **Failures:** a failed send is logged and never fails the search. Keys never appear in payloads or errors.

**Graph:** `understand` is the first node. It writes `status`, `filters` (the jsonb for `search_items`), `query_text` (what Retrieve will embed), `notes` and `message` to the state. There's no HTTP route yet.

## Trade-offs

- **Search text goes to Groq, Cloudflare and Langfuse.** Groq may keep logs for up to 30 days unless zero data retention is turned on (see 0002). The privacy notice must say so.
- **The fallback tries every provider on any failure**, including errors that a retry wouldn't fix, such as a 401 from a bad key. That's deliberate: one broken provider shouldn't stop search, and the trace shows why each attempt failed.
- **The day boundaries are product choices** (05:00, 17:00, and the weekend starting on Friday evening). They're constants in code, not config, because they're part of what "tonight" means. They're tested and recorded here.
- **The provider fixtures are synthetic.** They're built from documented response formats, not recorded, because this step makes no live calls. They're labelled as synthetic, and get replaced after the first live call.
- **A config change reaches the agent within a minute**, not instantly.

## Metrics and results

- **Agent tests:** 217 pass with no network. An autouse fixture fails any real HTTP request. They cover:
  - the strict schema (20 refused replies), and the `SearchFilters` contract with migration 010
  - every `when` value, before 05:00, inside a weekend, both clock changes, past and impossible dates, and leap years
  - place matching against the real 53 seeded areas (including no partial matches), distances, prices and notes
  - both adapters' requests and every outcome
  - the fallback chain (12 failure types), config order and prompt, bad config, the store's caching and stale fallback, and the OTLP payload
  - 21 eval cases (`evals/understand/cases.jsonl`), with expected filters written by hand
- **Jobs and db:** `understand_context` matches the seeds and is executable by `service_role` only. The config rows are checked in `rls_and_indexes.sql`.
- **Deliberate breaks:** 24 in the agent and 4 in migration 011, and all 28 fail the suite. The first round missed three:
  - text around a fenced reply, a gap since filled
  - partial place names, a gap since filled
  - a "fixed-hours weekend" that turned out not to be a bug, because Python adds a `timedelta` to an aware datetime in wall-clock time. It was replaced by a real bug (summing in UTC), which the tests catch.
- **Memory:** httpx moved from dev to main dependencies, but `langgraph-sdk` already installed it, so the image's packages are unchanged.
- **Still to do** (0002 carry-overs, which need the accounts):
  - one live call per model, recording token counts and rate-limit headers
  - turning on Groq's zero data retention
  - confirming no card is attached
  - replacing the synthetic fixtures with recordings
- **Supabase:** 011 isn't applied yet. Apply it with its checksum update in the same run (0006), and set `SUPABASE_SERVICE_KEY`, the provider keys and `LANGFUSE_*` on Render. Nothing calls the node until the search route exists.

## Changes

- **2026-10-27: asking before searching, holidays and the radius rule (step 1.5, migration 013).**
  - **Why:** the eval-set review (step 1.6) found requests the parser either guessed at or couldn't express: "next Friday" on a Tuesday, "Bonfire Night this Thursday" (5 Nov is next Thursday), "cheap", "£60 total", "near me" with no location, "something fun", and events with no known date. The owner's rules (in the project docs `eval-ambiguity-rules-1.6`, `eval-location-rules-1.6`, `clarify-chips-1.6` and `festival-dates-2026-2027`): when a request could mean more than one thing, ask rather than guess.
  - **New status:** `needs_clarification`, with `missing` (in this order: `location`, `date`, `budget`, `price_basis`, `event_date`, `category`) and one question per reason. The filters keep everything that did resolve, so the answer fills only the gap. `/parse` returns `missing` and `questions` (`reason`, `question`, `choices`) and logs `missing` in a new `queries` column. These rows count against the daily cap, because they spent a model call.
  - **Who decides:** the model reports what it read, and code decides what to ask (principle 5). `UnderstoodQuery` gains `price_scope` (`each`, `total`), `near_me`, `event` (a named event with no date given) and `unclear` (`date`, `budget`, `category`: what the model couldn't pin down). `when` gains `qualifier` (`this`, `next`), a weekday alongside a date, and kind `holiday`. `understand_prompt` v2 (migration 013) describes them. v1 is kept, but v1 replies no longer validate.
  - **Rules in code** (`resolve.py`):

    | Request | Result |
    |---|---|
    | A weekday, bare or "this" | The next one, today included ("Tuesday" on a Tuesday is today) |
    | "Next" + a weekday | A week today if it's that day; otherwise ask (`date`) |
    | A date with a weekday | Resolves if they agree ("Friday 6 November", "5th Nov next Thursday"); otherwise ask (`date`) |
    | A time the kinds can't express, or a seasonal activity with no time | Ask (`date`) |
    | A named event with no date | Ask (`event_date`) |
    | "Cheap" with no amount | Ask (`budget`) |
    | A price for the whole group ("£60 total") | Ask (`price_basis`). "£60 each" or a plain "under £60" is per ticket |
    | "Near me", or a distance with no place, and no point sent | Ask (`location`). This replaces the `distance_without_place` note |
    | Nothing about what kind of thing ("something fun") | Ask (`category`), with chips: Music, Comedy, Food & Drink, Arts, Nightlife, Family, Sport, Just surprise me |

  - **Location:** `/parse` accepts an optional `location` (`lat`, `lon`): a pin, or the browser's position for "near me". A named area wins over it. Around the user's point the radius is the slider's default, 1 mile (1.609344 km). The point is used for that search only: it's left out of the logged filters and the Langfuse trace.
  - **Radius (unchanged, now tested as a rule):** a named area uses its stored `default_radius_km`, and a stated distance overrides it, converted to km and capped at 100 km. The UI slider (0.5 to 6 miles, default 1 mile, miles on cards) is the frontend's, so the parser doesn't cap at 6 miles.
  - **Bank holidays:** from a snapshot of gov.uk's England and Wales list (`agent/app/understand/bank_holidays.json`, 2019 to 2028), refreshed with `agent/scripts/refresh_bank_holidays.py`. A test fails when it covers less than six months ahead. Searches never call gov.uk.
    - Named moving days: Good Friday, Easter Sunday, Easter Monday, Easter weekend, the three May and August bank holidays, "the bank holiday" and "the bank holiday weekend".
    - A long weekend runs from 17:00 on the evening before its first day off to 05:00 after its last, like any weekend. So Easter weekend 2027 is Thu 25 Mar 17:00 to Tue 30 Mar 05:00. The seed doc wrote Sat 27 to Mon 29 Mar, and the eval follows this rule instead.
    - Days whose date never changes (Christmas Day, Boxing Day, Bonfire Night, Valentine's Day…) are plain dates to the model, so "Boxing Day" is 26 Dec, not the substitute day off on the 28th.
  - **Not done here:** months ("in March"), date ranges ("14 to 15 November"), "any Saturday", seasons, and a festival calendar. Those requests ask or drop the date, and the eval (0014) counts them as failures.
  - **Tests:** the agent has 364 tests, all passing with no network. The jobs tests (361) and the db checks pass against a local `postgis/postgis:17-3.6`. 21 deliberate breaks in the agent and 4 in migration 013 all fail the suite or the db checks.
  - **Supabase:** apply 013 with `migrate.sh`, and deploy the agent in the same window. Once 013 is applied, prompt v2 is live, and an agent still running the old code would reject v2 replies, so search would rest until the deploy. The other order fails the same way. Apply 013 first, then deploy at once.
