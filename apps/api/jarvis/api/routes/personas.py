"""Personas: presets and your own (PLAN.md 10.3.2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.errors import Conflict, NotFound
from jarvis.services.personas import (
    PRESETS,
    delete_persona,
    library,
    list_personas,
    save_persona,
    slug,
)

router = APIRouter(prefix="/v1/personas", tags=["personas"])


class PersonaIn(BaseModel):
    name: str = Field(max_length=60)
    instructions: str = Field(max_length=2000)
    description: str = Field(default="", max_length=200)


@router.get("")
async def personas(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    return await list_personas(session, user.id)


@router.put("/{key}")
async def put_persona(
    key: str, body: PersonaIn, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    key = slug(key)
    if key in PRESETS or key in library():
        raise Conflict("Built-in personas cannot be edited; save yours under another name")
    return await save_persona(
        session, user.id, key, name=body.name, instructions=body.instructions,
        description=body.description,
    )


@router.delete("/{key}", status_code=204)
async def remove_persona(key: str, user: CurrentUser, session: SessionDep) -> None:
    if not await delete_persona(session, user.id, slug(key)):
        raise NotFound("Persona")
