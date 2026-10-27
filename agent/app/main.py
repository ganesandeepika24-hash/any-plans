# Stage 0 sync test.
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.config import Settings, get_settings
from app.graph import build_graph
from app.parse import Parser
from app.querylog import SupabaseQueryLog
from app.routes import health, parse
from app.understand.llm import OpenAICompatibleClient
from app.understand.node import Understander
from app.understand.store import SupabaseStore
from app.understand.tracing import LangfuseTracer, NullTracer, Tracer


def build_understander(
    settings: Settings, http: httpx.AsyncClient, store: SupabaseStore | None = None
) -> Understander:
    """Nothing here touches the network: config, areas and models load on first use."""
    tracer: Tracer
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        tracer = LangfuseTracer(
            http,
            settings.langfuse_host,
            settings.langfuse_public_key,
            settings.langfuse_secret_key,
            release=settings.config_version,
        )
    else:
        tracer = NullTracer(release=settings.config_version)
    return Understander(
        store=store or SupabaseStore(http, settings.rest_url, settings.supabase_service_key),
        llm=OpenAICompatibleClient(
            http,
            settings.groq_api_key,
            settings.cloudflare_api_token,
            settings.cloudflare_account_id,
        ),
        tracer=tracer,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # fail fast if required config is missing
    async with httpx.AsyncClient() as http:
        store = SupabaseStore(http, settings.rest_url, settings.supabase_service_key)
        understander = build_understander(settings, http, store)
        app.state.graph = build_graph(understander)
        app.state.parser = Parser(
            understander,
            store,
            SupabaseQueryLog(http, settings.rest_url, settings.supabase_service_key),
            release=settings.config_version,
        )
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="Any Plans? agent", lifespan=lifespan)
    # Public routes. Protected routers take dependencies=[Depends(require_user)].
    app.include_router(health.router)
    # Protected: require_user is a dependency of the route itself.
    app.include_router(parse.router)
    return app


app = create_app()
