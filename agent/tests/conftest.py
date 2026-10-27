import os

os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """No test may reach a real host: LLM providers, Langfuse and Supabase are faked with
    httpx.MockTransport or test doubles. A real request fails the test.

    Tests marked slow are exempt: they fetch the embedding model from Hugging Face when
    CI's model cache is cold, and they call no LLM, Langfuse or Supabase."""
    if request.node.get_closest_marker("slow"):
        return

    async def refuse_async(self, request):
        raise RuntimeError(f"network disabled in tests: {request.url.host}")

    def refuse(self, request):
        raise RuntimeError(f"network disabled in tests: {request.url.host}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture
def anyio_backend():
    return "asyncio"
