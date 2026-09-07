"""Habit streaks and meeting prep (FEATURES-50 #33, #38)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings
from jarvis.services.proactivity import (
    coming_up,
    habit_streaks,
    meeting_prep,
    owed_replies,
    what_mattered,
)

router = APIRouter(prefix="/v1/proactivity", tags=["proactivity"])


@router.get("/streaks")
async def streaks(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    tz = user.timezone or get_settings().timezone
    return await habit_streaks(session, user.id, tz=tz)


@router.get("/upcoming")
async def upcoming(
    user: CurrentUser, session: SessionDep, hours: int = 48
) -> list[dict[str, Any]]:
    return await coming_up(session, user.id, hours=hours)


@router.get("/meeting")
async def meeting(user: CurrentUser, session: SessionDep) -> dict[str, Any] | None:
    tz = user.timezone or get_settings().timezone
    return await meeting_prep(session, user.id, tz=tz)


@router.get("/owed")
async def owed(
    user: CurrentUser, session: SessionDep, limit: int = 10
) -> list[dict[str, Any]]:
    """Dropped-thread finder (#28): people who asked you something you may owe a reply."""
    return await owed_replies(session, user.id, limit=limit)


@router.get("/digest")
async def digest(
    user: CurrentUser, session: SessionDep, limit: int = 5
) -> list[dict[str, Any]]:
    """"What mattered" (#25): the few things that need you now, ranked — for the home glance."""
    return await what_mattered(session, user.id, limit=limit)
