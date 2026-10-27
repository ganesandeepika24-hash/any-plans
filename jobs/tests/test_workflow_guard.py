"""Ingest workflows stay manual until their source is approved for a schedule.

Ticketmaster: while decision 0001 doesn't say CLEARED, ingest-ticketmaster.yml may
only be started by hand. Its triggers must be exactly {workflow_dispatch}, however
the YAML is written, and no other workflow may mention Ticketmaster, so nothing can
call it on a timer either.

Skiddle: ingest-skiddle.yml runs nightly and by hand. Its triggers must be exactly
{schedule, workflow_dispatch}, and the schedule is refused unless 0001 contains
"Skiddle schedule: APPROVED", which is recorded with the approval email's conditions.

OSM venues: ingest-osm-venues.yml runs weekly and by hand. Its triggers must be exactly
{schedule, workflow_dispatch}, and a scheduled run, which has no inputs, must write.
The ingest step's script is run with a stub `uv` to check that no --dry-run is passed.

Embed events: embed-events.yml is manual only (ingest embeds nightly), with dry_run
defaulting to true.
"""

import os
import re
import shutil
import subprocess

import pytest
import yaml

from tests.conftest import REPO

WORKFLOWS = REPO / ".github" / "workflows"
TICKETMASTER_WORKFLOW = WORKFLOWS / "ingest-ticketmaster.yml"
SKIDDLE_WORKFLOW = WORKFLOWS / "ingest-skiddle.yml"
DECISION_0001 = REPO / "docs" / "decisions" / "0001-sources-and-terms.md"
SKIDDLE_APPROVAL = "Skiddle schedule: APPROVED"
SKIDDLE_TRIGGERS = {"schedule", "workflow_dispatch"}


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


def skiddle_failures(workflow_text: str, decision_text: str) -> list[str]:
    found = triggers(workflow_text)
    if found != SKIDDLE_TRIGGERS:
        return [f"triggers are {sorted(found)}, expected {sorted(SKIDDLE_TRIGGERS)}"]
    approved = any(line.startswith(SKIDDLE_APPROVAL) for line in decision_text.splitlines())
    return [] if approved else [f"0001 has no line starting {SKIDDLE_APPROVAL!r}"]


def test_skiddle_workflow_is_scheduled_and_manual():
    assert skiddle_failures(SKIDDLE_WORKFLOW.read_text(), DECISION_0001.read_text()) == []


def test_skiddle_schedule_is_refused_without_the_approval_line():
    without = "\n".join(
        line
        for line in DECISION_0001.read_text().splitlines()
        if not line.startswith(SKIDDLE_APPROVAL)
    )
    assert SKIDDLE_APPROVAL not in without
    assert skiddle_failures(SKIDDLE_WORKFLOW.read_text(), without) == [
        f"0001 has no line starting {SKIDDLE_APPROVAL!r}"
    ]


def test_skiddle_approval_must_be_its_own_line():
    mentioned = 'The schedule waits for "Skiddle schedule: APPROVED" to be recorded.\n'
    assert skiddle_failures(SKIDDLE_WORKFLOW.read_text(), mentioned) != []


@pytest.mark.parametrize(
    "text",
    [
        "on:\n  workflow_dispatch:\n",
        "on:\n  schedule:\n    - cron: '0 2 * * *'\n",
        "on:\n  schedule:\n    - cron: '0 2 * * *'\n  workflow_dispatch:\n  push:\n",
        "on: [schedule, workflow_dispatch, workflow_call]\n",
        "on: {schedule: [{cron: '0 2 * * *'}], workflow_dispatch: {}, repository_dispatch: {}}\n",
    ],
    ids=[
        "manual only",
        "schedule only",
        "plus push",
        "plus workflow_call",
        "plus repository_dispatch",
    ],
)
def test_skiddle_rejects_any_other_trigger_set(text):
    assert skiddle_failures(text, f"{SKIDDLE_APPROVAL} (conditions recorded 26 Oct 2026)\n") != []


def test_snapshot_workflow_is_scheduled_and_manual():
    workflow = WORKFLOWS / "snapshot-prices.yml"
    assert triggers(workflow.read_text()) == {"schedule", "workflow_dispatch"}
    assert "ticketmaster" not in workflow.read_text().lower()


OSM_WORKFLOW = WORKFLOWS / "ingest-osm-venues.yml"
OSM_TRIGGERS = {"schedule", "workflow_dispatch"}
OSM_STEP = "Ingest OSM venues"
OSM_JOB = ["run", "python", "-m", "jobs.osm_venues"]
EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
STUB_UV = '#!/bin/sh\nprintf "%s\\n" "$@"\n'


def on_section(text: str) -> dict:
    doc = yaml.safe_load(text) or {}
    return doc.get("on", doc.get(True))


def osm_ingest_args(workflow_text: str, inputs: dict[str, str], bin_dir) -> list[str]:
    """Run the ingest step's script as GitHub would and return the job's arguments.

    A scheduled run has an empty inputs context, so every ${{ inputs.* }} is "".
    """
    doc = yaml.safe_load(workflow_text)
    (job,) = doc["jobs"].values()
    step = next(s for s in job["steps"] if s.get("name") == OSM_STEP)
    assert "if" not in job and "if" not in step, "the ingest step must not be skipped"

    def render(value) -> str:
        def expression(match: re.Match) -> str:
            name = match.group(1)
            if name.startswith("inputs."):
                return inputs.get(name.removeprefix("inputs."), "")
            if name.startswith("secrets."):
                return "unused"
            raise AssertionError(f"can't evaluate ${{{{ {name} }}}} in the ingest step")

        return EXPRESSION.sub(expression, str(value))

    uv = bin_dir / "uv"
    uv.write_text(STUB_UV)
    uv.chmod(0o755)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    env |= {k: render(v) for k, v in step.get("env", {}).items()}
    bash = shutil.which("bash")
    assert bash, "bash is needed to run the workflow step"
    out = subprocess.run(  # noqa: S603 (our own committed workflow, not input)
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", render(step["run"])],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert out[: len(OSM_JOB)] == OSM_JOB
    return out[len(OSM_JOB) :]


def test_osm_venues_workflow_is_weekly_and_manual():
    text = OSM_WORKFLOW.read_text()
    assert triggers(text) == OSM_TRIGGERS
    assert on_section(text)["schedule"] == [{"cron": "0 5 * * 0"}]
    assert "ticketmaster" not in text.lower()


def test_osm_venues_manual_inputs_are_unchanged():
    inputs = on_section(OSM_WORKFLOW.read_text())["workflow_dispatch"]["inputs"]
    assert {name: (i["type"], i["default"]) for name, i in inputs.items()} == {
        "dry_run": ("boolean", True),
        "diagnose": ("boolean", False),
    }


def test_osm_venues_scheduled_run_writes(tmp_path):
    assert osm_ingest_args(OSM_WORKFLOW.read_text(), {}, tmp_path) == []


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        ({"dry_run": "true", "diagnose": "false"}, ["--dry-run"]),
        ({"dry_run": "false", "diagnose": "false"}, []),
        ({"dry_run": "false", "diagnose": "true"}, ["--dry-run", "--diagnose"]),
        ({"dry_run": "true", "diagnose": "true"}, ["--dry-run", "--diagnose"]),
    ],
    ids=["manual default", "manual write", "diagnose", "diagnose and dry run"],
)
def test_osm_venues_manual_runs_keep_their_flags(tmp_path, inputs, expected):
    assert osm_ingest_args(OSM_WORKFLOW.read_text(), inputs, tmp_path) == expected


def test_osm_scheduled_write_check_catches_a_dry_run_default(tmp_path):
    text = OSM_WORKFLOW.read_text()
    old = 'elif [ "$DRY_RUN" = "true" ]; then'
    assert text.count(old) == 1
    broken = text.replace(old, 'elif [ "$DRY_RUN" != "false" ]; then')
    assert osm_ingest_args(broken, {}, tmp_path) == ["--dry-run"]


EMBED_WORKFLOW = WORKFLOWS / "embed-events.yml"


def test_embed_events_workflow_is_manual_only():
    # Ingest runs the embedding step after dedupe every night (decision 0009).
    text = EMBED_WORKFLOW.read_text()
    assert guard_failures(text) == []
    assert on_section(text)["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    assert yaml.safe_load(text)["concurrency"]["group"] == "db-writes"
    assert "ticketmaster" not in text.lower()


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
