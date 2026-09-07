"""Disliked reminders: mute a sender/channel and see what you've muted (#14)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.errors import NotFound
from jarvis.services import reminders

router = APIRouter(prefix="/v1/reminders", tags=["reminders"])


@router.post("/dislike/{task_id}")
async def dislike(
    task_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """Dislike a reminder: mute its sender so future ones stop, and dismiss the ones
    already in the list from that sender."""
    result = await reminders.dislike_task(session, user.id, task_id)
    await session.commit()
    return result


@router.get("/mutes")
async def mutes(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    """The Muted section — every sender/channel you've disliked."""
    return await reminders.list_mutes(session, user.id)


@router.delete("/mutes/{mute_id}")
async def remove_mute(
    mute_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> dict[str, bool]:
    if not await reminders.unmute(session, user.id, mute_id):
        raise NotFound("Mute")
    await session.commit()
    return {"ok": True}
