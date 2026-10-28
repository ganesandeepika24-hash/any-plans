"""POST /search: free text in, events out (stage 1 step 1, decision 0016).

The same auth, body and daily cap as /parse. POST, not GET, so the user's location
never appears in a URL, where access logs would record it. An ok parse is searched;
needs_clarification and ai_resting come back as /parse returns them, with no results.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from app.auth import User, require_user
from app.parse import ParseRequest
from app.search import Searcher, SearchResponse

router = APIRouter()


def get_searcher(request: Request) -> Searcher:
    return request.app.state.searcher


@router.post("/search", response_model=SearchResponse)
async def search(
    body: ParseRequest,
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
    outcome = await searcher.search(user_id, body.query, body.location)
    background.add_task(searcher.record, outcome)
    return outcome.response
