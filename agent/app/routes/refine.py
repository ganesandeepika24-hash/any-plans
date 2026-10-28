"""POST /refine: an earlier query, changed by a follow-up, then searched (stage 1 step 2,
decision 0018).

The same auth and daily cap as /parse and /search. The earlier query must be the user's
own: an unknown id and someone else's both get 404, before any model call or cap use. An
earlier query that didn't resolve (a question, a rest, the cap) gets 409: answer or
search again instead. The location, if the earlier search used the user's own point,
has to be sent again, since it was never stored.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import User, require_user
from app.parse import Location
from app.querylog import QueryLogError
from app.routes.search import get_searcher
from app.search import PriorNotFound, PriorNotRefinable, Searcher, SearchResponse
from app.understand.node import MAX_QUERY_CHARS

router = APIRouter()


class RefineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: UUID  # the earlier /parse, /search or /refine query
    message: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)  # "make it cheaper"
    location: Location | None = None  # needed again if the earlier search used it

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message is blank")
        return value


@router.post("/refine", response_model=SearchResponse)
async def refine(
    body: RefineRequest,
    background: BackgroundTasks,
    user: Annotated[User, Depends(require_user)],
    searcher: Annotated[Searcher, Depends(get_searcher)],
) -> SearchResponse:
    try:
        user_id = UUID(user.id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    try:
        outcome = await searcher.refine(user_id, body.query_id, body.message, body.location)
    except PriorNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such earlier search") from exc
    except PriorNotRefinable as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That search didn't finish, so it can't be refined"
        ) from exc
    except QueryLogError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Can't read the earlier search right now"
        ) from exc
    background.add_task(searcher.record, outcome)
    return outcome.response
