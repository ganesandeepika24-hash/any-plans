# Stage 0 sync test.
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.graph import build_graph
from app.routes import health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_settings()  # fail fast if required config is missing
    app.state.graph = build_graph()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Any Plans? agent", lifespan=lifespan)
    # Public routes. Protected routers take dependencies=[Depends(require_user)].
    app.include_router(health.router)
    return app


app = create_app()
