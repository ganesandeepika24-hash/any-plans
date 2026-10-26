"""Supabase JWT verification as a FastAPI dependency.

Attach `require_user` to protected routers only:

    router = APIRouter(dependencies=[Depends(require_user)])

Tokens are verified against the project's JWKS with the algorithm pinned to
ES256, and `aud` and `iss` are checked. There is no shared-secret path.
"""

from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import Settings, get_settings

ALGORITHM = "ES256"
AUDIENCE = "authenticated"
JWKS_CACHE_SECONDS = 600
JWKS_TIMEOUT_SECONDS = 5

_bearer = HTTPBearer(auto_error=False)


class User(BaseModel):
    id: str
    email: str | None = None
    role: str | None = None


@lru_cache
def get_jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        get_settings().jwks_url,
        cache_keys=True,
        lifespan=JWKS_CACHE_SECONDS,
        timeout=JWKS_TIMEOUT_SECONDS,
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    jwks: Annotated[jwt.PyJWKClient, Depends(get_jwks_client)],
) -> User:
    if credentials is None:
        raise _unauthorized()
    token = credentials.credentials
    try:
        signing_key = jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.PyJWKClientConnectionError as exc:
        # The JWKS endpoint is unreachable: a server problem, not a bad token.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth keys unavailable",
        ) from exc
    except (jwt.PyJWKClientError, jwt.InvalidTokenError) as exc:
        raise _unauthorized() from exc
    return User(id=claims["sub"], email=claims.get("email"), role=claims.get("role"))
