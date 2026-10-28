import re
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# An origin is a scheme and a host, with a port only for local development: no path,
# no trailing slash, no wildcard. Browsers send exactly this form.
_HTTPS_ORIGIN = re.compile(
    r"https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+"
)
_LOCAL_ORIGIN = re.compile(r"http://(localhost|127\.0\.0\.1)(:\d{1,5})?")


class Settings(BaseSettings):
    """Runtime settings, read from environment variables.

    Secrets live in Render and GitHub Actions; nothing here has a secret default.
    A missing provider key turns that provider off (the Understand node falls back);
    missing Langfuse keys turn tracing off.
    """

    model_config = SettingsConfigDict(extra="ignore")

    supabase_url: str
    config_version: str = "dev"

    # PostgREST access to config and understand_context (decision 0012).
    supabase_service_key: str | None = None

    groq_api_key: str | None = None
    cloudflare_api_token: str | None = None
    cloudflare_account_id: str | None = None

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # The browser origins allowed to call the agent: the live site and the Lovable
    # project, comma-separated, set in Render. Exact addresses only; the regex is for
    # preview deploys and is unset unless the owner chooses to allow them. Neither set
    # means no CORS headers, so no browser can call the agent.
    cors_origins: str = ""
    cors_origin_regex: str | None = None

    @field_validator("cors_origins")
    @classmethod
    def _exact_origins(cls, value: str) -> str:
        for origin in _split(value):
            if not (_HTTPS_ORIGIN.fullmatch(origin) or _LOCAL_ORIGIN.fullmatch(origin)):
                raise ValueError(
                    f"CORS origin {origin!r} must be https://host (or http://localhost:port),"
                    " with no path, trailing slash or wildcard"
                )
        return value

    @field_validator("cors_origin_regex")
    @classmethod
    def _https_pattern(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        value = value.strip()
        if not value.startswith(r"https://"):
            raise ValueError("CORS origin regex must start with https://")
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"CORS origin regex doesn't compile: {exc}") from exc
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return _split(self.cors_origins)

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/rest/v1"


def _split(value: str) -> list[str]:
    return [o.strip() for o in value.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
