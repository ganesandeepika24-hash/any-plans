"""Only sources decision 0001 allows run on a schedule (migration 025, jobs.schedule_gate),
and each source's pace stays inside its documented limits.

The gate is checked in jobs.ingest.main() before any API call. A scheduled run is
GITHUB_EVENT_NAME=schedule; manual runs are never stopped by it.
"""

import json
import re

import pytest

from jobs import ingest, schedule_gate
from jobs.sources import skiddle, ticketmaster
from tests.conftest import REPO
from tests.test_workflow_guard import DECISION_0001, SKIDDLE_APPROVAL, ticketmaster_cleared

MIGRATION_025 = REPO / "db" / "migrations" / "025_ticketmaster_compliance.sql"


class FakeDb:
    def __init__(self, value):
        self.value = value

    def config_value(self, key):
        assert key == schedule_gate.KEY
        return self.value


SHIPPED = {"skiddle": True, "ticketmaster": False}


@pytest.mark.parametrize(
    ("source", "event", "refused"),
    [
        ("ticketmaster", "schedule", True),
        ("skiddle", "schedule", False),
        ("ticketmaster", "workflow_dispatch", False),
        ("ticketmaster", None, False),  # run locally
        ("skiddle", "workflow_dispatch", False),
        ("osm", "schedule", True),  # not listed: not allowed
    ],
)
def test_only_allowed_sources_run_on_a_schedule(source, event, refused):
    assert (schedule_gate.refusal(FakeDb(SHIPPED), source, event) is not None) is refused


@pytest.mark.parametrize("value", [None, [], "skiddle", {"skiddle": "true"}])
def test_an_unreadable_config_allows_no_schedule(value):
    for source in ("skiddle", "ticketmaster"):
        assert schedule_gate.refusal(FakeDb(value), source, "schedule") is not None
    assert schedule_gate.refusal(FakeDb(value), "skiddle", "workflow_dispatch") is None


def shipped_schedule() -> dict:
    sql = MIGRATION_025.read_text()
    value = re.search(r"values \('ingest_schedule', 1, '(.*?)'", sql, re.S).group(1)
    return json.loads(value)


def test_the_shipped_config_matches_decision_0001():
    text = DECISION_0001.read_text()
    skiddle_approved = any(line.startswith(SKIDDLE_APPROVAL) for line in text.splitlines())
    assert shipped_schedule() == {
        "skiddle": skiddle_approved,
        "ticketmaster": ticketmaster_cleared(text),
    }
    assert shipped_schedule() == SHIPPED


# Rate limits ------------------------------------------------------------------------


def test_ticketmaster_stays_inside_the_discovery_api_limits():
    # 5 requests a second, 5,000 a day, and size x page < 1000 (Discovery API docs, 0001).
    assert ticketmaster.MIN_INTERVAL >= 1 / 5
    assert ticketmaster.PER_RUN_CAP <= ticketmaster.DAILY_STOP < 5000
    assert ticketmaster.DEEP_PAGING_LIMIT == 1000
    assert ticketmaster.REQUESTED_SIZE < ticketmaster.DEEP_PAGING_LIMIT


def test_skiddle_stays_inside_its_own_stops():
    assert skiddle.MIN_INTERVAL >= 1
    assert skiddle.PER_RUN_CAP <= skiddle.DAILY_STOP


# The gate in ingest.main() --------------------------------------------------------------


class ReachedRun(Exception):
    """ingest.run was called: the gate let the run through."""


@pytest.fixture
def stub_run(monkeypatch, db_url):
    monkeypatch.setenv("SUPABASE_DB_URL", db_url)
    monkeypatch.setenv("TICKETMASTER_KEY", "KEY")
    monkeypatch.setenv("SKIDDLE_KEY", "KEY")

    def run(*args, **kwargs):
        raise ReachedRun

    def no_client(*args, **kwargs):
        raise AssertionError("an HTTP client was opened before the gate refused the run")

    monkeypatch.setattr(ingest, "run", run)
    return no_client


@pytest.mark.db
def test_a_scheduled_ticketmaster_run_stops_before_any_call(monkeypatch, stub_run, capsys):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setattr(ingest.httpx, "Client", stub_run)
    assert ingest.main(["ticketmaster"]) == 3
    assert "may not ingest on a schedule" in capsys.readouterr().err


@pytest.mark.db
@pytest.mark.parametrize(
    ("source", "event"),
    [
        ("skiddle", "schedule"),
        ("ticketmaster", "workflow_dispatch"),
        ("skiddle", "workflow_dispatch"),
    ],
)
def test_allowed_runs_go_ahead(monkeypatch, stub_run, source, event):
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    with pytest.raises(ReachedRun):
        ingest.main([source, "--dry-run"])


@pytest.mark.db
def test_a_manual_run_off_github_goes_ahead(monkeypatch, stub_run):
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    with pytest.raises(ReachedRun):
        ingest.main(["ticketmaster", "--dry-run"])
