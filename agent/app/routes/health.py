from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings

router = APIRouter()


@router.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    """Public liveness check. No database call."""
    return {"status": "ok", "config_version": settings.config_version}
