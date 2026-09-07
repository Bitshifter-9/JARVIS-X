"""Life search: one box over everything (second-brain #26)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.search import life_search

router = APIRouter(prefix="/v1/search", tags=["search"])


@router.get("")
async def search(
    user: CurrentUser,
    session: SessionDep,
    q: str = Query(min_length=2, max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
) -> dict[str, Any]:
    results = await life_search(session, user.id, q, limit=limit)
    return {"query": q, "results": results}
