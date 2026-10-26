import httpx
import pytest

from jobs.http import CallBudget, CapReached, RequestFailed
from tests.conftest import FakeClock, client_for


def ok(request):
    return httpx.Response(200, json={"ok": True})


def test_requests_are_at_least_the_minimum_interval_apart():
    clock = FakeClock()
    sent_at = []

    def handler(request):
        sent_at.append(clock.now)
        return ok(request)

    client = client_for(handler, min_interval=0.5, clock=clock)
    for _ in range(4):
        client.get_json("https://api.example/x", {})
    gaps = [b - a for a, b in zip(sent_at, sent_at[1:], strict=False)]
    assert gaps and all(g >= 0.5 for g in gaps)


def test_429_is_retried_after_the_retry_after_header():
    responses = [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json=[1])]
    clock = FakeClock()
    client = client_for(lambda r: responses.pop(0), clock=clock)
    assert client.get_json("https://api.example/x", {}) == [1]
    assert 7 in clock.sleeps
    assert client.budget.used == 2  # retries count against the quota


def test_errors_never_include_the_query_string():
    client = client_for(lambda r: httpx.Response(500))
    with pytest.raises(RequestFailed) as error:
        client.get_json("https://api.example/x", {"apikey": "SECRET-KEY"})
    assert "SECRET" not in str(error.value)
    assert client.budget.used == 4  # first try plus three retries


def test_per_run_cap():
    budget = CallBudget(per_run=2, daily_limit=100, used_today=0)
    budget.take()
    budget.take()
    with pytest.raises(CapReached, match="per-run cap of 2"):
        budget.take()


def test_daily_stop_counts_calls_made_earlier_today():
    budget = CallBudget(per_run=2000, daily_limit=4500, used_today=4499)
    budget.take()
    with pytest.raises(CapReached, match="daily stop of 4500"):
        budget.take()
