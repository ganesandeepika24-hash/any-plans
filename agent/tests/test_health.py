from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def test_health_ok_with_default_config_version(monkeypatch):
    monkeypatch.delenv("CONFIG_VERSION", raising=False)
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "config_version": "dev"}


def test_health_reads_config_version_from_env(monkeypatch):
    monkeypatch.setenv("CONFIG_VERSION", "2026-10-26.1")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.json()["config_version"] == "2026-10-26.1"


def test_health_needs_no_token():
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert "www-authenticate" not in response.headers


def test_startup_compiles_graph():
    app = create_app()
    with TestClient(app):
        result = app.state.graph.invoke({"query": "jazz tonight"})
    assert result["status"] == "not_implemented"
