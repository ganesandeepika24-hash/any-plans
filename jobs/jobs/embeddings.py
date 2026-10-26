"""Event embeddings with fastembed: the same model as the agent (decision 0005).

tests/test_embeddings_model.py fails if this model name drifts from agent/app/embeddings.py.
"""

from functools import lru_cache

from fastembed import TextEmbedding

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIMENSIONS = 384


@lru_cache(maxsize=1)
def get_model() -> TextEmbedding:
    return TextEmbedding(MODEL_NAME)


def embed(texts: list[str]) -> list[list[float]]:
    vectors = [v.tolist() for v in get_model().embed(texts)]
    for v in vectors:
        if len(v) != DIMENSIONS:
            raise ValueError(f"expected {DIMENSIONS} dimensions, got {len(v)}")
    return vectors
