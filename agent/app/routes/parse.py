"""POST /parse: free text in, search_items filters out (decision 0013).

Needs a valid Supabase JWT. needs_clarification is a normal 200 reply: the browser asks
the questions it carries instead of searching. So is ai_resting: the browser then falls
back to filter-only search, which reads events directly (decision 0013).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from app.auth import User, require_user
from app.parse import Parser, ParseRequest, ParseResponse

router = APIRouter()


def get_parser(request: Request) -> Parser:
    return request.app.state.parser


@router.post("/parse", response_model=ParseResponse)
async def parse(
    body: ParseRequest,
    background: BackgroundTasks,
    user: Annotated[User, Depends(require_user)],
    parser: Annotated[Parser, Depends(get_parser)],
) -> ParseResponse:
    try:
        user_id = UUID(user.id)
    except ValueError as exc:
        # Supabase user ids are UUIDs; anything else isn't one of our tokens.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    outcome = await parser.parse(user_id, body.query, body.location)
    background.add_task(parser.record, outcome)
    return outcome.response
