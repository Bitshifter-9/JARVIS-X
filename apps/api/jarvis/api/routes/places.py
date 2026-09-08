"""Significant places, learned from coarse location (#5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings
from jarvis.services.places import places

router = APIRouter(prefix="/v1/places", tags=["places"])


@router.get("")
async def list_places(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    tz = user.timezone or get_settings().timezone
    return await places(session, user.id, tz=tz)
