"""Only sources that decision 0001 allows may ingest on a schedule.

A scheduled run (GitHub's GITHUB_EVENT_NAME is "schedule") of a source that config
`ingest_schedule` (migration 025) doesn't mark true stops before any API call. So does
a scheduled run when the config can't be read: no schedule is allowed by default. A
manual run (workflow_dispatch, or locally) is never stopped here. The workflow guard
(tests/test_workflow_guard.py) keeps Ticketmaster's workflow manual; this is the same
rule at run time, in case a schedule reaches the job some other way.
"""

from jobs.db import Database

KEY = "ingest_schedule"
SCHEDULED = "schedule"


def refusal(db: Database, source: str, event_name: str | None) -> str | None:
    """Why this run must not go ahead, or None if it may."""
    if event_name != SCHEDULED:
        return None
    allowed = db.config_value(KEY)
    if not isinstance(allowed, dict):
        return f"{KEY} is missing or malformed, so no source may run on a schedule"
    if allowed.get(source) is not True:
        return f"{source} may not ingest on a schedule ({KEY}; decision 0001)"
    return None
