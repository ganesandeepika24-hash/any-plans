import time
from typing import Annotated

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm

from app.auth import User, get_jwks_client, require_user
from app.config import get_settings
from app.main import create_app

KID = "test-key-1"
ISSUER = "https://test-project.supabase.co/auth/v1"


class FakeJWKSClient(jwt.PyJWKClient):
    """A real PyJWKClient whose fetch returns an in-memory JWKS (no network)."""

    def __init__(self, jwks: dict | None = None, error: Exception | None = None):
        super().__init__("https://jwks.invalid/jwks.json")
        self._jwks = jwks
        self._error = error

    def fetch_data(self):
        if self._error:
            raise self._error
        return self._jwks


def _jwk(public_key, kid: str) -> dict:
    return {**ECAlgorithm.to_jwk(public_key, as_dict=True), "kid": kid, "alg": "ES256"}


@pytest.fixture(scope="module")
def signing_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def jwks_client(signing_key):
    return FakeJWKSClient({"keys": [_jwk(signing_key.public_key(), KID)]})


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "user-123",
        "email": "someone@example.com",
        "role": "authenticated",
        "aud": "authenticated",
        "iss": ISSUER,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _token(key, claims=None, kid=KID, alg="ES256") -> str:
    return jwt.encode(claims or _claims(), key, algorithm=alg, headers={"kid": kid})


def _client(jwks_client) -> TestClient:
    # Test-only protected route; the app itself has none yet.
    router = APIRouter(dependencies=[Depends(require_user)])

    @router.get("/protected")
    def protected(user: Annotated[User, Depends(require_user)]) -> dict:
        return user.model_dump()

    app = create_app()
    app.include_router(router)
    app.dependency_overrides[get_jwks_client] = lambda: jwks_client
    return TestClient(app)


def _get(client, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.get("/protected", headers=headers)


def test_valid_token_returns_user(jwks_client, signing_key):
    response = _get(_client(jwks_client), _token(signing_key))
    assert response.status_code == 200
    assert response.json() == {
        "id": "user-123",
        "email": "someone@example.com",
        "role": "authenticated",
    }


def test_missing_header_is_401(jwks_client):
    response = _get(_client(jwks_client), None)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_garbage_token_is_401(jwks_client):
    assert _get(_client(jwks_client), "not-a-jwt").status_code == 401


def test_expired_token_is_401(jwks_client, signing_key):
    token = _token(signing_key, _claims(exp=int(time.time()) - 10))
    assert _get(_client(jwks_client), token).status_code == 401


@pytest.mark.parametrize("missing", ["exp", "sub", "aud", "iss"])
def test_missing_required_claim_is_401(jwks_client, signing_key, missing):
    token = _token(signing_key, _claims(**{missing: None}))
    assert _get(_client(jwks_client), token).status_code == 401


def test_wrong_audience_is_401(jwks_client, signing_key):
    token = _token(signing_key, _claims(aud="anon"))
    assert _get(_client(jwks_client), token).status_code == 401


def test_wrong_issuer_is_401(jwks_client, signing_key):
    token = _token(signing_key, _claims(iss="https://other-project.supabase.co/auth/v1"))
    assert _get(_client(jwks_client), token).status_code == 401


def test_issuer_follows_supabase_url(monkeypatch, signing_key):
    monkeypatch.setenv("SUPABASE_URL", "https://other-project.supabase.co/")
    get_settings.cache_clear()
    jwks = FakeJWKSClient({"keys": [_jwk(signing_key.public_key(), KID)]})
    token = _token(signing_key, _claims(iss="https://other-project.supabase.co/auth/v1"))
    assert _get(_client(jwks), token).status_code == 200


def test_unknown_kid_is_401(jwks_client):
    other = ec.generate_private_key(ec.SECP256R1())
    assert _get(_client(jwks_client), _token(other, kid="unknown")).status_code == 401


def test_wrong_key_with_trusted_kid_is_401(jwks_client):
    other = ec.generate_private_key(ec.SECP256R1())
    assert _get(_client(jwks_client), _token(other)).status_code == 401


def test_hs256_token_is_401(jwks_client):
    token = _token("a-shared-secret-that-is-at-least-32-bytes", alg="HS256")
    assert _get(_client(jwks_client), token).status_code == 401


def test_alg_none_is_401(jwks_client):
    token = jwt.encode(_claims(), None, algorithm="none", headers={"kid": KID})
    assert _get(_client(jwks_client), token).status_code == 401


def test_jwks_unreachable_is_503(signing_key):
    jwks = FakeJWKSClient(error=jwt.PyJWKClientConnectionError("down"))
    assert _get(_client(jwks), _token(signing_key)).status_code == 503


def test_jwks_url_and_issuer_derived_from_supabase_url():
    settings = get_settings()
    assert settings.jwks_url == ISSUER + "/.well-known/jwks.json"
    assert settings.jwt_issuer == ISSUER
