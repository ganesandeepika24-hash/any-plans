# 0004: Agent host

Status: proposed (2026-10-25). Owner: deepika. Revisit: after the first week of live traffic, and whenever Render changes its free tier.

## Context

The agent is a stateless FastAPI and LangGraph service in a Docker container. It holds the secret keys (Groq, Cloudflare, Tavily, Supabase service key, Langfuse). Requirements: a public HTTPS URL, a plausible uptime for demos, and no surprise charges.

## Options considered

| Host | Free tier | Card | Memory | Cold start | Verdict |
|---|---|---|---|---|---|
| **Render** free web service | Permanent; 750 instance-hours a month for the workspace; spins down after 15 minutes idle | Requested for verification in some accounts ($1 hold, cancelled). Free services don't bill unless a paid service is chosen. | 512 MB | About 1 minute | **Chosen** |
| Google Cloud Run | Per billing account: 2M requests, 180k vCPU-seconds, 360k GiB-seconds a month (us-central1 published; London to verify) | Required (billing account) | Configurable | A few seconds | Fallback. Charges are possible above the free tier, so it needs `max-instances` and a budget alert. Budget alerts only notify and don't stop spending. |
| Hugging Face Spaces (Docker) | Docker Spaces need a paid plan ($9/month PRO, which needs a card) | Yes | 16 GB on paid CPU | 30 to 60 seconds | Rejected |
| Railway | 30-day trial with $5 credit; $1 a month after; card needed for paid plans | Sources disagree | 512 MB to 1 GB | Fast | Rejected for permanent use |
| Fly.io | No free tier for new organisations; trial of 2 VM hours or 7 days | Required after trial | Configurable | Fast | Rejected |
| Koyeb | One free instance; card with $29 hold required since February 2026 | Yes | 512 MB | 1 to 5 seconds | Rejected |

## Decision

Render free web service. A card is accepted for account verification only (decided 25 Oct 2026). Paid services are never selected. The `agent/` folder is deployed as a Docker service with root directory `agent/`, and the build filter is `agent/**`.

## Trade-offs

- **Render:** a card can't produce a bill unless we choose a paid service, so the worst case is downtime. Its risks are 512 MB of memory (the fastembed model plus LangGraph might not fit; to test) and a one-minute wake-up after 15 minutes idle.
- **Cloud Run:** more memory and faster wake-ups, but charges become possible once usage exceeds the free tier. We'd need `max-instances` set and a spend-cap or kill-switch in place. More setup, more moving parts.
- **Why not HF or Fly or Railway or Koyeb:** each either needs a card with an ongoing bill, or has no permanent free tier.

## Memory check (required before step 0.4 is ticked)

Deploy the agent on Render with the fastembed model baked into the image. Record:
- Peak memory during an idle start and during a plan request.
- Whether the container is killed (out of memory).

If the container exceeds 512 MB, switch to Cloud Run. Keep the same image and change only the host.

## Metrics

| Measure | Target |
|---|---|
| Uptime during demo hours (live check every few minutes) | Track; no fixed target in stage 2 |
| Cold-start time after sleep | Under 90 seconds (NFR: loading state shown) |
| Out-of-memory restarts | 0 |
| Monthly bill | £0. Check weekly. |

## Reversal

Move hosts by changing one environment variable (`AGENT_URL` in the frontend) and redeploying the same image. The frontend only calls `src/lib/api.ts`, so no other code changes.

## Open checks

- Render sign-up (25 Oct 2026): no card was requested. Account created. Free web service not yet deployed (step 0.4). If a card is requested later, record it here.
- Confirm the free instance is still 512 MB and 750 hours at the time of sign-up.
- Confirm no paid service or add-on gets selected by default.