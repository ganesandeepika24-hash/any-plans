"""Settings from environment variables. Secrets come from GitHub Actions secrets only."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_url: str
    ticketmaster_key: str | None
    skiddle_key: str | None


def from_env() -> Settings:
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        raise RuntimeError("SUPABASE_DB_URL is not set")
    return Settings(
        db_url=db_url,
        ticketmaster_key=os.environ.get("TICKETMASTER_KEY") or None,
        skiddle_key=os.environ.get("SKIDDLE_KEY") or None,
    )
