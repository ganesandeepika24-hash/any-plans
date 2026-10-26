from pathlib import Path

import pytest

from app.embeddings import DIMENSIONS, MODEL_NAME, embed


@pytest.mark.slow
def test_embed_batch_returns_384_dimension_vectors():
    texts = ["jazz in Soho tonight", "free museums near King's Cross", "kids' theatre"]
    vectors = embed(texts)
    assert len(vectors) == len(texts)
    assert all(len(v) == DIMENSIONS for v in vectors)


@pytest.mark.slow
def test_similar_texts_are_closer():
    a, b, c = embed(["live jazz music", "jazz concert", "tax return deadline"])

    def dot(x, y):
        return sum(i * j for i, j in zip(x, y, strict=True))

    assert dot(a, b) > dot(a, c)


def test_dockerfile_bakes_the_same_model():
    dockerfile = (Path(__file__).parent.parent / "Dockerfile").read_text()
    assert MODEL_NAME in dockerfile
