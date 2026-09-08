"""Digital twin: answer as you (#19)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.twin import answer_as_you

router = APIRouter(prefix="/v1/twin", tags=["twin"])


class TwinIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@router.post("")
async def ask_twin(body: TwinIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """"What would I say?" — answered in your voice, grounded on your self-model."""
    return await answer_as_you(session, user.id, body.question)
