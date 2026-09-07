"""Instant contextual recall: ask your life a question, get a sourced answer (#23)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.recall import ask_life

router = APIRouter(prefix="/v1/recall", tags=["recall"])


class RecallIn(BaseModel):
    question: str = Field(min_length=3, max_length=300)


@router.post("")
async def recall(body: RecallIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Answer from everything captured — mail, deadlines, chat, memory — each claim sourced.
    The model call fires only because you asked; it degrades to the ranked sources."""
    return await ask_life(session, user.id, body.question)
