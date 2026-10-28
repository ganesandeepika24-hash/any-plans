# Stage 0 sync test.
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.embeddings import embed
from app.graph import build_graph
from app.parse import Parser
from app.querylog import SupabaseQueryLog
from app.routes import health, parse, refine, search
from app.search import Searcher, SearchNode, SupabaseSearch
from app.understand.llm import OpenAICompatibleClient
from app.understand.node import Understander
from app.understand.store import SupabaseStore
from app.understand.tracing import LangfuseTracer, NullTracer, Tracer
from app.usage import MeteredLLM, SupabaseUsage

CORS_MAX_AGE_S = 600  # browsers cache a preflight for 10 minutes


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
    store = store or SupabaseStore(http, settings.rest_url, settings.supabase_service_key)
    # Every provider call is counted against its daily limit first (decision 0016).
    llm = MeteredLLM(
        OpenAICompatibleClient(
            http,
            settings.groq_api_key,
            settings.cloudflare_api_token,
            settings.cloudflare_account_id,
        ),
        store,
        SupabaseUsage(http, settings.rest_url, settings.supabase_service_key),
    )
    return Understander(store=store, llm=llm, tracer=tracer)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # fail fast if required config is missing
    async with httpx.AsyncClient() as http:
        store = SupabaseStore(http, settings.rest_url, settings.supabase_service_key)
        understander = build_understander(settings, http, store)
        query_log = SupabaseQueryLog(http, settings.rest_url, settings.supabase_service_key)
        search_node = SearchNode(
            SupabaseSearch(http, settings.rest_url, settings.supabase_service_key), embed
        )
        app.state.graph = build_graph(understander, search_node)
        app.state.parser = Parser(understander, store, query_log, release=settings.config_version)
        app.state.searcher = Searcher(app.state.parser, search_node, query_log)
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="Any Plans? agent", lifespan=lifespan)
    # The browser app is on another origin. Only the addresses in CORS_ORIGINS may call
    # the agent; auth is a bearer token, so no cookies or credentials mode.
    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=settings.cors_origin_regex,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
        max_age=CORS_MAX_AGE_S,
    )
    # Public routes. Protected routers take dependencies=[Depends(require_user)].
    app.include_router(health.router)
    # Protected: require_user is a dependency of the route itself.
    app.include_router(parse.router)
    app.include_router(search.router)
    app.include_router(refine.router)
    return app


app = create_app()
