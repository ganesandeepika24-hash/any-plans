# Agent service image for Render (root directory: agent/).

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
# Bake the embedding model into the image. Keep in sync with app/embeddings.py.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN /app/.venv/bin/python -c \
    "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')" \
    # huggingface_hub writes some cache files as root-only; the runtime user must read them.
    && chmod -R a+rX /opt/fastembed

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /opt/fastembed /opt/fastembed
COPY app ./app
ENV PATH=/app/.venv/bin:$PATH \
    FASTEMBED_CACHE_PATH=/opt/fastembed \
    HF_HUB_OFFLINE=1 \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
# Render sets PORT; default to 8000 locally.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
