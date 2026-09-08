"""Media diary: what you watched/read, with a takeaway (#6)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.errors import NotFound
from jarvis.services import media_diary as svc

router = APIRouter(prefix="/v1/media-diary", tags=["media-diary"])


class NoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


@router.get("")
async def diary(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    return await svc.media_diary(session, user.id)


@router.post("/{source_id}/note")
async def note(
    source_id: uuid.UUID, body: NoteIn, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    out = await svc.set_note(session, user.id, source_id, body.note)
    if out is None:
        raise NotFound("Media item")
    await session.commit()
    return out
