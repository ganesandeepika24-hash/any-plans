# 0002: LLM provider

Status: proposed (2026-10-25). Owner: deepika. Revisit: after the first eval run in stage 1, and again in stage 4.

## Context

The agent uses an LLM in two places:
- **Understand** (node 1): turns the user's text into a `SearchFilters` object (structured output).
- **Rank and plan** (node 5): picks 3 to 5 candidates from a list of about 20 and writes a short plan.

Constraints: zero running cost, no card on file for the LLM provider, UK end users (so no Gemini free tier: Google's Gemini API Additional Terms, effective 23 March 2026, allow only Paid Services for users in the UK/EEA/CH), and a fallback so a single provider outage doesn't stop plans.

## Options considered

| Option | Cost | Card needed | Data use | Limits (free) | Verdict |
|---|---|---|---|---|---|
| Gemini 2.5 / 3.x free tier | £0 | Only with billing | Used for product improvement; banned for UK users | varies | **Ruled out** for UK end users |
| Claude Haiku 5.5 (API) | About £1 to £4 a month at our volume | Yes (prepaid credits) | Not trained on by default via API | None free | **Deferred**. Pro plan has no API credits. Max or Team plans include credits, so this remains an option if that changes. |
| **Groq** (gpt-oss-20b for Understand, gpt-oss-120b for Rank and plan) | £0 | No (per secondary sources; to verify at sign-up) | Services Agreement: not used for training; logs up to 30 days for abuse/troubleshooting; zero-retention toggle in console | Per org: about 30 requests a minute, 1,000 a day, 200,000 tokens a day on gpt-oss-120b (secondary sources) | **Chosen as primary** |
| **Cloudflare Workers AI** (gpt-oss-20b and 120b) | £0 | No (to verify) | Cloudflare says it doesn't train on Workers AI content (quoted by a third-party summary; primary not checked) | 10,000 Neurons a day, shared across models | **Chosen as fallback** |

## Decision

- **Primary:** Groq. Understand uses gpt-oss-20b with strict JSON-schema output, validated by Pydantic. Rank and plan uses gpt-oss-120b.
- **Fallback:** Cloudflare Workers AI, same model family, used when Groq returns 429, 5xx, or times out.
- **Last resort:** the existing keyword search (A-01): if both providers fail, return filter-only results, with the message "AI search is resting; filters still work".
- Model names, provider order and fallback thresholds live in the config table, not in code (Q-06).

## Trade-offs

- **Groq** has the most generous no-card free tier, but its limits are per organisation and can change. Its data policy is acceptable for a public demo, but logs are kept for up to 30 days. Our privacy notice must say this.
- **Cloudflare** has a smaller daily budget (about 100 full searches on my estimate, against about 60 on Groq's 120b), but it runs on separate infrastructure, so an outage at one provider is unlikely to affect the other.
- **Claude** would give the best quality and the clearest data terms, but it needs a card and funded credits. We keep it as a config option for the stage 4 comparison, not for launch.
- **Why two providers and not one:** a single provider outage would stop AI search for everyone. Two providers cover that for free, at the cost of a second integration (a small one, behind the `LLMClient` interface).

## Capacity estimate (unmeasured)

Assumptions: Understand prompt about 700 tokens in and 100 out; Rank prompt about 3,300 in (20 candidates) and 400 out.
- Groq, 120b rank step: about 60 full searches a day (200k tokens a day limit).
- Groq, 20b parse step: about 280 parses a day.
- Cloudflare: about 100 full searches a day, shared across models.
- Per-user cap: 10 AI searches a day (config value).

These are estimates. Replace them with measured token counts after the first eval run.

## Metrics and acceptance tests

| Measure | Target | Source |
|---|---|---|
| Date and area parse accuracy on the eval set | Baseline recorded in stage 1; target 90% after stage 4 | Langfuse evals |
| Plan returned with fallback forced (A-07 test) | 100% of simulated 429s still return a plan | CI test |
| Tokens per search (measured) | Recorded per model per day | `usage_daily` |
| Provider error rate | Below 2% over 30 days | `queries` table |
| p95 plan latency (warm) | Under 8 seconds (NFR) | Langfuse traces |

## Reversal

Changing provider is a config change plus one eval run. If Groq limits drop or terms change, we move Rank to Cloudflare or Claude, then re-run evals before switching in production.

## Sign-up outcomes (25 Oct 2026)

- **Groq:** account and API key created. No card requested. Key saved in password manager as `GROQ_API_KEY`.
- **Cloudflare:** account created with Workers AI enabled. No card requested. Token and account ID saved.
- **Upstash:** skipped. Not needed for MVP evals, observation or reflection (Supabase tables and Langfuse cover them). Reconsider if per-minute rate limits or cache latency become a problem.

## Carried to step 1.5 (not blocking step 0.2)

- Check Groq's limits page for `gpt-oss-20b` and `gpt-oss-120b`, then replace the capacity estimates above with the published values.
- Turn on Groq's zero-data-retention toggle if the free plan allows it. If not, the privacy notice must say that search text may be kept for up to 30 days.
- Read Groq's services agreement (the primary source) on data use and logging.
- Confirm Cloudflare's rate limits from its limits page.
- Make one test call per model, and record token counts and response-header limits as the first measured figures.
- Confirm no payment method is attached to either account, and don't upgrade to a paid tier.