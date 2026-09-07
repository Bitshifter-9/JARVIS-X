"""Interest drift: what you're into now vs drifting from (#17)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.interests import interest_drift

router = APIRouter(prefix="/v1/interests", tags=["interests"])


@router.get("")
async def interests(user: CurrentUser, session: SessionDep) -> dict[str, list[dict[str, Any]]]:
    return await interest_drift(session, user.id)
