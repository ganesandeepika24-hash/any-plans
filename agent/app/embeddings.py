"""Text embeddings with fastembed (ONNX all-MiniLM-L6-v2, 384 dimensions).

The model is baked into the Docker image (see Dockerfile) and loaded on first use.
"""

from functools import lru_cache

from fastembed import TextEmbedding

# Keep in sync with the model download step in the Dockerfile.
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
