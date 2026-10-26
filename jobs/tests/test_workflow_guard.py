"""Ticketmaster stays unscheduled until decision 0001 says CLEARED.

While it isn't cleared, ingest-ticketmaster.yml may only be started by hand: its
triggers must be exactly {workflow_dispatch}, however the YAML is written. No other
workflow may mention Ticketmaster, so nothing can call it on a timer either.
"""

import re

import pytest
import yaml

from tests.conftest import REPO

WORKFLOWS = REPO / ".github" / "workflows"
TICKETMASTER_WORKFLOW = WORKFLOWS / "ingest-ticketmaster.yml"
DECISION_0001 = REPO / "docs" / "decisions" / "0001-sources-and-terms.md"


def triggers(text: str) -> set[str]:
    doc = yaml.safe_load(text) or {}
    # YAML 1.1 reads an unquoted `on` key as the boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(t) for t in on}
    if isinstance(on, dict):
        return {str(t) for t in on}
    return set()


def ticketmaster_cleared(text: str) -> bool:
    row = next(
        line for line in text.splitlines() if line.startswith("| Ticketmaster Discovery API")
    )
    status = row.strip().strip("|").split("|")[-1]
    return "NOT CLEARED" not in status and re.search(r"\bCLEARED\b", status) is not None


def guard_failures(workflow_text: str) -> list[str]:
    found = triggers(workflow_text)
    return [] if found == {"workflow_dispatch"} else [f"triggers are {sorted(found)}"]


@pytest.fixture
def not_cleared():
    if ticketmaster_cleared(DECISION_0001.read_text()):
        pytest.skip("0001 says Ticketmaster is CLEARED; scheduling is allowed")


def test_ticketmaster_workflow_is_manual_only(not_cleared):
    assert guard_failures(TICKETMASTER_WORKFLOW.read_text()) == []


def test_no_other_workflow_mentions_ticketmaster(not_cleared):
    others = [
        p.name
        for p in WORKFLOWS.glob("*.y*ml")
        if p != TICKETMASTER_WORKFLOW and "ticketmaster" in p.read_text().lower()
    ]
    assert others == []


def test_0001_currently_reads_not_cleared():
    assert not ticketmaster_cleared(DECISION_0001.read_text())


@pytest.mark.parametrize(
    "text",
    [
        "on:\n  workflow_dispatch:\n  schedule:\n    - cron: '0 3 * * *'\n",
        "on: {workflow_dispatch: {}, schedule: [{cron: '0 3 * * *'}]}\n",
        "on: [workflow_dispatch, schedule]\n",
        "on: schedule\n",
        "'on':\n  \"schedule\":\n    - cron: '0 3 * * *'\n  workflow_dispatch:\n",
        "x-cron: &nightly\n  - cron: '0 3 * * *'\n"
        "on:\n  workflow_dispatch:\n  schedule: *nightly\n",
        "on:\n  workflow_dispatch:\n  workflow_call:\n",
        "on:\n  push:\n    branches: [main]\n",
        "name: no triggers at all\n",
    ],
    ids=["block", "flow", "list", "string", "quoted", "anchor", "workflow_call", "push", "none"],
)
def test_guard_rejects_any_other_trigger(text):
    assert guard_failures(text) != []


@pytest.mark.parametrize(
    "text",
    [
        "on:\n  workflow_dispatch:\n    inputs:\n      dry_run: {type: boolean}\n",
        "on: workflow_dispatch\n",
        "'on': [workflow_dispatch]\n",
    ],
)
def test_guard_accepts_manual_only(text):
    assert guard_failures(text) == []
