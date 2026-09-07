"""Relationship cadence: who you keep up with, and who you've gone quiet on (#16)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.relationships import relationships

router = APIRouter(prefix="/v1/relationships", tags=["relationships"])


@router.get("")
async def list_relationships(
    user: CurrentUser, session: SessionDep, limit: int = 20
) -> list[dict[str, Any]]:
    return await relationships(session, user.id, limit=limit)
