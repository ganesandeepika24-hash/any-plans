"""The near-me phrases in docs/api.md are the ones the active prompt reads as near me
(decision 0017, step 1.4 near-me in one search). The Lovable app spots these before an
AI search and reads the device location first, so the search is sent once. If the
prompt and the doc drift apart, the app's list goes stale: this fails first."""

import re

from tests.understand_support import REPO

MIGRATIONS = REPO / "db" / "migrations"
API_DOC = REPO / "docs" / "api.md"


def active_prompt(migrations=MIGRATIONS) -> tuple[int, str]:
    """The understand_prompt with the highest version any migration inserts, as
    set_active_config leaves it after the migrations run."""
    found = []
    for path in sorted(migrations.glob("*.sql")):
        for m in re.finditer(
            r"values \('understand_prompt', (\d+),.*?\$prompt\$(.*?)\$prompt\$",
            path.read_text(),
            re.S,
        ):
            found.append((int(m.group(1)), m.group(2)))
    return max(found)


def prompt_phrases(prompt: str) -> list[str]:
    """The quoted phrases in the prompt's near_me line."""
    (line,) = [ln for ln in prompt.splitlines() if ln.strip().startswith("- near_me:")]
    inside = re.search(r"\((.*?)\)", line).group(1)
    return re.findall(r'"([^"]+)"', inside)


def doc_phrases(doc: str) -> list[str]:
    """The backticked items under the doc's "Near-me phrases" heading."""
    section = re.search(r"#### Near-me phrases\n(.*?)(?=\n#{2,4} )", doc, re.S).group(1)
    return re.findall(r"^- `([^`]+)`$", section, re.M)


def test_the_doc_lists_exactly_the_active_prompts_near_me_phrases():
    version, prompt = active_prompt()
    phrases = prompt_phrases(prompt)
    assert phrases, "the prompt's near_me line lists no phrases"
    assert doc_phrases(API_DOC.read_text()) == phrases, (
        f"docs/api.md's near-me phrases differ from understand_prompt v{version}'s: "
        "update the doc, then the Lovable app's list"
    )


def test_the_doc_names_the_prompt_version_it_follows():
    version, _ = active_prompt()
    section = re.search(r"#### Near-me phrases\n(.*?)(?=\n- )", API_DOC.read_text(), re.S)
    assert f"`understand_prompt` v{version}" in section.group(1)


def test_a_later_prompt_version_is_the_one_read(tmp_path):
    # A new migration with v7 takes over from v6, so a prompt change can't slip past.
    for path in sorted(MIGRATIONS.glob("*.sql")):
        (tmp_path / path.name).write_text(path.read_text())
    (tmp_path / "999_prompt.sql").write_text(
        "insert into public.config (key, version, value_json, updated_by, is_active)\n"
        "values ('understand_prompt', 99, jsonb_build_object('system', $prompt$x\n"
        '  - near_me: true if ("near me", "close by") and names no place.\n'
        "$prompt$), 'test', false);\n"
    )
    version, prompt = active_prompt(tmp_path)
    assert version == 99 and prompt_phrases(prompt) == ["near me", "close by"]
