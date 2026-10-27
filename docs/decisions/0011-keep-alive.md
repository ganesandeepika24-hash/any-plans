# 0011: Keep-alive

Status: accepted (2026-10-27). Owner: deepika. Revisit: if Supabase changes its pausing rule, or if the nightly jobs stop running for any length of time.

## Context

Supabase pauses Free plan projects with low activity. Its guide (supabase.com/docs/guides/platform/free-project-pausing, read 27 Oct 2026) says:

> "Supabase pauses Free Plan projects that show low activity over a 7-day period to save server resources."
>
> "A Free plan project is considered inactive if it does not receive sufficient user database activity over the past week."
>
> "Typically a few user requests to the database each day over the previous week is enough".

A Supabase staff reply in GitHub discussion #13121 says the check looks at database activity (queries and transactions), not just connections. That's community-forum guidance, not documentation.

The nightly jobs already query the database every day: Skiddle ingest at 02:00, snapshots at 03:00 and deletes at 04:00 (UTC). The risk is the gap if they stop: a broken secret, a failing source, or GitHub pausing scheduled workflows. A paused project takes the site down until someone restores it by hand.

## Options considered

| Option | Verdict |
|---|---|
| Rely on the nightly jobs | No backstop if they stop together, for example after a secret rotation. |
| An HTTP request to the REST API | Would need the project URL and a key as new Actions secrets. The guide counts database activity, and a REST call reaches the database only indirectly. |
| `pg_cron` inside Supabase | Runs inside the project, so its activity may not count as "user" activity. It can't help once the project is paused. |
| **A GitHub Actions job every 3 days** that runs real queries over the existing `SUPABASE_DB_URL` | **Chosen.** No new secrets. Real queries in real transactions, recorded in `job_runs` (principle 7). |

## Decision

- **Workflow:** `keepalive.yml`, cron `"0 6 */3 * *"`, plus manual dispatch, in the `db-writes` concurrency group.
  - Day-of-month `*/3` fires on days 1, 4, … 31 and restarts each month, so pings are never more than 3 days apart. A test walks two years, leap day included, to check this.
  - 06:00 UTC is clear of the 02:00 to 05:00 jobs.
- **Job:** `python -m jobs.keepalive`.
  - It inserts a `job_runs` row (`job 'keepalive'`, `city_id` null), counts the events the public can see, and finishes the row with `fetched` set to that count.
  - The count uses `events_public_read`'s rules, and a test compares it with what `anon` sees.
- **Guard test:** the triggers must be exactly `{schedule, workflow_dispatch}`, with that cron, and the workflow must run `jobs.keepalive`.

## Trade-offs

- **Every 3 days, not daily.** The guide's "a few requests each day" describes typical sufficient activity, not a hard rule. The daily jobs supply that today. Three days still leaves at least 4 days of margin inside the 7-day window if the nightly jobs stop.
- **GitHub disables scheduled workflows in a repository with no commits for 60 days**, and the keep-alive doesn't count as repository activity. During a long quiet spell, GitHub's warning email is the signal to act.
- **One small row every 3 days** goes into `job_runs`: about 120 a year.

## Metrics and results

- **Tests:** 3 for the job (the `job_runs` row, the count matching `anon`'s view, and `main()` opening a writable connection) and 2 for the workflow guard.
- **Deliberate breaks:** a count that includes duplicates, a run that never finishes, an 8-day cron, and the wrong module. All four fail the suite.
- **To record after the first scheduled run:** the `keepalive` rows in `job_runs`, and whether Supabase's dashboard shows the project as active.
