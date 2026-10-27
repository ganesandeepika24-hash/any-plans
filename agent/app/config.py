from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/rest/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
