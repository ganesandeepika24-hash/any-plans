"""The secret scan's one exception stays narrow: Supabase publishable keys only.

.gitleaks.toml allows sb_publishable_ values, which CLAUDE.md lets the front end carry
and Lovable commits. Secret keys, other look-alikes, and the default rules must still
fail the scan."""

import base64
import json
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


def test_gitleaksignore_holds_only_the_one_fake_jwt():
    # Fingerprints only, and only the fake JWT this test once held as a literal.
    lines = [
        ln
        for ln in (REPO / ".gitleaksignore").read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    assert lines == [
        "bf6b5685b7acd5727fc0d9e94bb65c360aa0647a:jobs/tests/test_secret_scan.py:jwt:44"
    ]


def test_publishable_keys_are_allowed():
    assert allowed("sb_publishable_" + BODY + "_-xyz")


def _jwt(role: str) -> str:
    """A JWT-shaped value, built here so no token-like literal sits in the repo for the
    scan to find."""
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return ".".join([part({"alg": "HS256"}), part({"role": role}), "c2ln"])


BODY = "A1b2C3d4E5f6G7h8I9j0"


@pytest.mark.parametrize(
    "secret",
    [
        "sb_secret_" + BODY,
        _jwt("service_role"),
        "sb_publishable_",  # nothing after the prefix
        "sb_publishable_short",
        "x" + "sb_publishable_" + BODY,  # not at the start
        "sb_publishable_" + BODY + " and more",  # not the whole value
        "gsk_" + BODY * 2,  # a Groq key
    ],
)
def test_everything_else_still_fails(secret):
    assert not allowed(secret)
