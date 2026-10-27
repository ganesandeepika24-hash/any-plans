"""Smoke-test POST /parse against a running agent (decision 0013).

Usage, from agent/:

    uv run python scripts/smoke_parse.py "jazz in Soho tonight under £20"

Everything secret comes from environment variables, never from code or the command
line (the only argument is the query):

    AGENT_URL                 the agent's base URL, e.g. https://<service>.onrender.com
    SUPABASE_URL              https://<project>.supabase.co
    SUPABASE_PUBLISHABLE_KEY  the project's publishable (anon) key
    SUPABASE_USER_TOKEN       a signed-in user's access token (a JWT)

It calls /parse and prints the full JSON response. If the response has a query_id, it
reads that row from queries through the Supabase REST API with the same token (migration
012 lets users read their own rows) and prints it; when the status is ai_resting it
also prints the row's attempts. The token is never printed.

Exit codes: 0 if /parse answered 200, 1 if it didn't or the row couldn't be read,
2 for a usage or environment problem.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ENV = ("AGENT_URL", "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_USER_TOKEN")
TIMEOUT_S = 60  # a cold Render instance takes about a minute to wake (decision 0004)
ROW_TRIES = 5  # the agent writes the row just after it replies
ROW_WAIT_S = 1.0
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def main(argv: list[str], env: dict[str, str], out=sys.stdout, err=sys.stderr) -> int:
    if len(argv) != 1 or not argv[0].strip():
        print('usage: smoke_parse.py "<query>"  (settings come from the environment)', file=err)
        return 2
    missing = [name for name in ENV if not env.get(name)]
    if missing:
        print(f"set these environment variables first: {', '.join(missing)}", file=err)
        return 2
    agent = env["AGENT_URL"].rstrip("/")
    supabase = env["SUPABASE_URL"].rstrip("/")
    for name, url in (("AGENT_URL", agent), ("SUPABASE_URL", supabase)):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            print(f"{name} must be an http(s) URL", file=err)
            return 2
        if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
            print(f"{name} must use https: the token would be sent in clear", file=err)
            return 2
    token = env["SUPABASE_USER_TOKEN"]

    status, body = _request(
        "POST",
        f"{agent}/parse",
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json.dumps({"query": argv[0]}).encode(),
    )
    print(f"POST /parse -> HTTP {status}", file=out)
    _print(body, out)
    if status != 200 or not isinstance(body, dict):
        return 1

    query_id = body.get("query_id")
    if not query_id:
        return 0
    row = _read_row(supabase, env["SUPABASE_PUBLISHABLE_KEY"], token, query_id, err)
    if row is None:
        return 1
    print(f"\nqueries row {query_id}:", file=out)
    _print(row, out)
    if body.get("status") == "ai_resting":
        print(f"\nattempts ({body.get('reason')}):", file=out)
        _print(row.get("attempts", []), out)
    return 0


def _read_row(
    supabase: str, publishable_key: str, token: str, query_id: str, err
) -> dict[str, Any] | None:
    query = urllib.parse.urlencode({"id": f"eq.{query_id}", "select": "*"})
    headers = {"apikey": publishable_key, "Authorization": f"Bearer {token}"}
    status, body = 0, None
    for attempt in range(ROW_TRIES):
        status, body = _request("GET", f"{supabase}/rest/v1/queries?{query}", headers)
        if status == 200 and isinstance(body, list) and body:
            return body[0]
        if status != 200:
            break
        if attempt < ROW_TRIES - 1:
            time.sleep(ROW_WAIT_S)
    if status == 200:
        print(
            f"queries row {query_id} not found after {ROW_TRIES} tries: the agent may have"
            " failed to log it, or migration 012 isn't applied",
            file=err,
        )
    else:
        print(f"reading queries failed: HTTP {status}", file=err)
        _print(body, err)
    return None


def _request(
    method: str, url: str, headers: dict[str, str], data: bytes | None = None
) -> tuple[int, Any]:
    """Returns (HTTP status, parsed JSON or text). Status 0 means no response."""
    # The URL is checked in main(): http(s) only, https unless local.
    request = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        return 0, f"no response: {reason}"


def _decode(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _print(value: Any, stream) -> None:
    if isinstance(value, str):
        print(value, file=stream)
    else:
        print(json.dumps(value, indent=2, ensure_ascii=False), file=stream)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], dict(os.environ)))
