"""Rediscover: an old note relevant to what you're doing now (#27)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.rediscover import rediscover

router = APIRouter(prefix="/v1/rediscover", tags=["rediscover"])


@router.get("")
async def get_rediscover(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return await rediscover(session, user.id)
