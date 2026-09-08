"""Communication coach: how you're keeping up with people, with fixes (#37)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings
from jarvis.services.comm_coach import comm_coach

router = APIRouter(prefix="/v1/comm-coach", tags=["comm-coach"])


@router.get("")
async def coach(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    tz = user.timezone or get_settings().timezone
    return await comm_coach(session, user.id, tz=tz)
