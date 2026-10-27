# Verification log: free tiers, terms and limits

Checked: 2026-10-25 (step 0.1 and follow-up). Rule: primary source wins; secondary sources are marked and must be re-checked before a step is ticked.

Status key: **V** = verified against the provider's own page (fetched or quoted in search); **S** = secondary source only; **X** = conflict or not yet checked.

## LLM and search

| Item | Claim | Status | Source | Action before use |
|---|---|---|---|---|
| Gemini free tier, UK end users | Paid Services only for users in UK, EEA, CH | **V** | ai.google.dev/gemini-api/terms (effective 23 Mar 2026, page updated 28 Apr 2026) | None: ruled out |
| Groq free tier limits | gpt-oss-20b and 120b: 30 RPM, 1,000 RPD, 8,000 TPM, 200,000 TPD | **V** for the Developer plan (console.groq.com/docs/rate-limits, read 27 Oct 2026); free plan not broken out | console.groq.com/docs/rate-limits | Check the account's own limits page (0002 follow-up) |
| Groq card needed | No card for free tier | **S** | Several guides | Confirm during sign-up |
| Groq data use | No retention by default; troubleshooting and abuse logs up to 30 days; ZDR open to all customers in Data Controls. Training: not stated on that page | **V** for retention and ZDR (console.groq.com/docs/your-data, read 27 Oct 2026); **S** for training | console.groq.com/docs/your-data | Turn on ZDR; read the Services Agreement for training use |
| Groq structured outputs | Strict mode only on gpt-oss 20b and 120b | **S** | console.groq.com docs snippet via search | Test with Pydantic in stage 1 |
| Groq uptime | Several 20-min to 2.5-h incidents in early 2026 | **S** | Status aggregators | Check groqstatus.com directly |
| Cloudflare free allocation | 10,000 Neurons a day, resets 00:00 UTC | **V** | developers.cloudflare.com/workers-ai/platform/pricing (updated 20 Oct 2026) | None |
| Cloudflare gpt-oss free | gpt-oss-20b and 120b not on the paid-only list | **V** | Same page | Re-check before launch |
| Cloudflare Neuron cost | gpt-oss-20b: 18,182 Neurons per M input, 27,273 per M output | **V** | Same page | None |
| Cloudflare rate limit | 300 requests a minute | **S** | developersdigest.tech (18 Oct 2026) | Check Cloudflare limits page |
| Cloudflare data use | No training on Workers AI content | **S** | Third-party quote | Check Cloudflare data-usage page |
| Cloudflare card needed | No | **X** not checked | — | Check at sign-up |
| Claude Haiku 5.5 price | $0.10 in, $0.50 out per M tokens up to 100k | **S** | Multiple secondary sources; Anthropic docs not fetched | Check platform.claude.com pricing if reconsidered |
| Claude API credits | Max 5x, Max 20x and Team include monthly API credits; Pro does not | **S** | Secondary sources | Check claude.com/pricing |

## Hosting

| Item | Claim | Status | Source | Action before use |
|---|---|---|---|---|
| Render free web service | 512 MB; spins down after 15 min; 750 hours a month per workspace | **V** (Render's own pages quoted in search) | render.com | Confirm at sign-up |
| Render card | Some accounts asked to add a card for verification ($1 hold, cancelled) | **V** (Render staff in community forum) | community.render.com | Test at sign-up; record outcome |
| Render paid services | Free services don't bill unless a paid service is selected | **S** | Secondary sources | Confirm the dashboard shows no paid add-ons |
| Hugging Face Docker Spaces | Need PRO subscription to create | **V** (HF manage-Spaces docs) | huggingface.co/docs/huggingface_hub | None: rejected |
| Fly.io | No free tier for new orgs; 2 VM hours or 7 days trial | **S** | Multiple secondary sources | None: rejected |
| Railway | $5 trial credit, 30 days; card needed | **X** (sources disagree on card) | railway.com/pricing vs. third-party | None: rejected |
| Koyeb | Card with $29 hold since Feb 2026 | **S** | Multiple secondary sources | None: rejected |
| Google Cloud Run free tier | 2M requests, 180k vCPU-s, 360k GiB-s a month per billing account | **V** (cloud.google.com/run/pricing quoted in search) | Google | Verify London (europe-west2) eligibility before use |
| Google budgets | Budgets alert but don't stop spending; spend-cap feature exists in preview | **V** (Google docs quoted) | cloud.google.com/billing | Only if Cloud Run is chosen |

## Data sources

| Item | Claim | Status | Source | Action before use |
|---|---|---|---|---|
| Ticketmaster Discovery API quota | 5,000 calls a day | **V** | developer.ticketmaster.com | None |
| Ticketmaster per-second rate | 5 a second (Getting Started) vs 2 a second (FAQ) | **X** conflict | Ticketmaster pages | Design for 2 a second |
| Ticketmaster paging | Deep paging limited to the first 1,000 items | **S** | Secondary (apis.io, PostHog docs) | Split London queries by date window |
| Ticketmaster terms on bulk use | Bulk fetching isn't expressly prohibited, but Ticketmaster may rate-limit calls "not primarily in response to direct user actions". **Our nightly ingest falls under this. Not cleared.** | **V** | Ticketmaster General Terms of Use (last updated 27 June 2023), read 26 Oct 2026 | Wait for written confirmation from Ticketmaster, or decide to proceed knowingly. Until then the ingest job runs on manual dispatch only (0001). |
| Skiddle | Free key; API for non-commercial use only; commercial use needs written approval from dev@skiddle.com | **V** (Skiddle API page) | skiddle.com/api | Keep as non-commercial portfolio demo; ask before any commercial use |
| OpenStreetMap / Overpass | Free; attribution (ODbL) | **X** not fetched this session | — | Read osm.org copyright page before stage 1 |
| Foursquare | 500 free Pro calls a month from 1 June 2026; Premium has no free tier | **S** (change notice quoted) | Secondary; foursquare.com/pricing conflicts | Dropped (decision 0003) |
| Tavily | 1,000 credits a month, no card | **V** (docs.tavily.com) | docs.tavily.com | Check cost per call below |
| Tavily cost per call | Advanced search: 2 credits. Basic is likely 1. | **X** (docs snippets truncated) | docs.tavily.com | **Decided:** advanced search, 16 calls a day, 950-credit monthly ceiling (16 × 2 × 31 ≈ 992, so the ceiling is the real stop). Confirm charging on failed calls on the dashboard. |
| Supabase free | 500 MB database, 50k MAU, 2 projects, pauses after 7 days idle | **V** (pausing: primary page quoted 27 Oct 2026; limits: multiple consistent sources) | supabase.com/pricing; supabase.com/docs/guides/platform/free-project-pausing | Keep-alive job every 3 days (decision 0011). The guide counts "user database activity"; "a few user requests to the database each day" is typically enough. |
| Langfuse Hobby | Free, no card, 50k units a month, 30 days data, 2 users | **V** (langfuse.com/pricing quoted) | langfuse.com | Units count traces, observations and scores; nightly evals will use a large share. Measure in stage 3. |
| Resend free | 3,000 a month, 100 a day; sending pauses at cap, no surprise overage | **V** (resend.com docs quoted) | resend.com/docs | Fine for daily digests |
| Upstash (optional) | 256 MB, 500k commands a month | **V** (upstash.com quoted) | upstash.com | Optional |

## Not yet checked

- Vercel or Netlify free tier and commercial-use terms.
- OpenFreeMap tile terms and attribution.
- TfL Unified API terms and keys.
- gov.uk bank holiday data licence (Open Government Licence; likely fine).
- Eventbrite: no public search API (decision: web fallback only).
- Google Places: not used.

## Priority order before step 0.2

1. Ticketmaster bulk-use terms (could affect the nightly job). **Read 26 Oct 2026: not cleared.**
2. Tavily credit cost (could exhaust the monthly allowance by mid-month).
3. Groq limits and data terms (read the primary sources).
4. Render card prompt (decides the host).
5. OSM attribution and Vercel terms (before stage 1).