"""Habit streaks and meeting prep (FEATURES-50 #33, #38)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings
from jarvis.services.proactivity import habit_streaks, meeting_prep

router = APIRouter(prefix="/v1/proactivity", tags=["proactivity"])


@router.get("/streaks")
async def streaks(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    tz = user.timezone or get_settings().timezone
    return await habit_streaks(session, user.id, tz=tz)


@router.get("/meeting")
async def meeting(user: CurrentUser, session: SessionDep) -> dict[str, Any] | None:
    tz = user.timezone or get_settings().timezone
    return await meeting_prep(session, user.id, tz=tz)
