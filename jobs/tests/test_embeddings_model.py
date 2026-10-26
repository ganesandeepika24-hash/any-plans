import ast

from jobs import embeddings
from tests.conftest import REPO


def constants(path) -> dict:
    tree = ast.parse(path.read_text())
    return {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
    }


def test_jobs_and_agent_use_the_same_embedding_model():
    agent = constants(REPO / "agent" / "app" / "embeddings.py")
    assert agent["MODEL_NAME"] == embeddings.MODEL_NAME
    assert agent["DIMENSIONS"] == embeddings.DIMENSIONS
