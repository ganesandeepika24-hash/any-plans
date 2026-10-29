"""The secret scan's one exception stays narrow: Supabase publishable keys only.

.gitleaks.toml allows sb_publishable_ values, which CLAUDE.md lets the front end carry
and Lovable commits. Secret keys, other look-alikes, and the default rules must still
fail the scan."""

import re
import tomllib

import pytest

from tests.conftest import REPO

CONFIG = tomllib.loads((REPO / ".gitleaks.toml").read_text())


def allowed(secret: str) -> bool:
    return any(
        re.search(pattern, secret)
        for allowlist in CONFIG["allowlists"]
        for pattern in allowlist["regexes"]
    )


def test_the_default_rules_stay_on():
    assert CONFIG["extend"] == {"useDefault": True}
    assert "rules" not in CONFIG  # no rule replaced or turned off


def test_there_is_one_allowlist_and_it_matches_the_secret_only():
    (allowlist,) = CONFIG["allowlists"]
    assert allowlist["regexTarget"] == "secret"
    assert set(allowlist) == {"description", "regexTarget", "regexes"}  # no paths or commits


def test_publishable_keys_are_allowed():
    assert allowed("sb_publishable_" + "A1b2C3d4E5f6G7h8I9j0_-xyz")


@pytest.mark.parametrize(
    "secret",
    [
        "sb_secret_" + "A1b2C3d4E5f6G7h8I9j0_-xyz",
        "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2lnbmF0dXJl",  # a JWT
        "sb_publishable_",  # nothing after the prefix
        "sb_publishable_short",
        "xsb_publishable_" + "A1b2C3d4E5f6G7h8I9j0",  # not at the start
        "sb_publishable_" + "A1b2C3d4E5f6G7h8I9j0 and more",  # not the whole value
        "gsk_" + "A1b2C3d4E5f6G7h8I9j0A1b2C3d4E5f6G7h8",  # a Groq key
    ],
)
def test_everything_else_still_fails(secret):
    assert not allowed(secret)
