"""Personalised micro-lessons from your knowledge gaps (#35)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.micro_lessons import micro_lesson

router = APIRouter(prefix="/v1/micro-lesson", tags=["micro-lessons"])


@router.get("")
async def lesson(
    user: CurrentUser, session: SessionDep, topic: str | None = None
) -> dict[str, Any]:
    """A 3-minute lesson for a topic, or your top knowledge gap if none is given."""
    return await micro_lesson(session, user.id, topic=topic)
