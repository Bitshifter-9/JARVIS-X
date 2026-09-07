"""Routines — "every morning at 7, brief me" (PLAN.md Phase 10.1)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.cron import PRESETS
from jarvis.db.models.ops import Routine
from jarvis.services.routines import RoutineService

router = APIRouter(prefix="/v1/routines", tags=["routines"])


class RoutineIn(BaseModel):
    name: str = Field(max_length=120)
    prompt: str = Field(max_length=4000)
    cron: str | None = Field(default=None, max_length=64)
    trigger: dict[str, Any] | None = None
    channel: str = Field(default="app", max_length=16)
    enabled: bool = True
    # "brief": just tell me (chat model over gathered context); "agent": do things.
    mode: str = Field(default="agent", pattern="^(brief|agent)$")


class RoutinePatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    prompt: str | None = Field(default=None, max_length=4000)
    cron: str | None = Field(default=None, max_length=64)
    trigger: dict[str, Any] | None = None
    channel: str | None = Field(default=None, max_length=16)
    enabled: bool | None = None
    mode: str | None = Field(default=None, pattern="^(brief|agent)$")


def _out(r: Routine) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "name": r.name,
        "kind": r.kind,
        "mode": "brief" if r.kind else "agent",
        "cron": r.cron,
        "trigger": r.trigger,
        "prompt": r.prompt,
        "channel": r.channel,
        "enabled": r.enabled,
        "next_run_at": r.next_run_at.isoformat() if r.next_run_at else None,
        "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
        "last_result": r.last_result,
    }


@router.get("/presets")
async def presets(user: CurrentUser) -> dict[str, str]:  # noqa: ARG001 — a session is required
    return PRESETS


@router.get("")
async def list_routines(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    return [_out(r) for r in await RoutineService(session).list(user.id)]


@router.post("", status_code=201)
async def create_routine(body: RoutineIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    fields = body.model_dump()
    kind = "brief" if fields.pop("mode") == "brief" else None
    return _out(await RoutineService(session).create(user.id, kind=kind, **fields))


@router.patch("/{routine_id}")
async def update_routine(
    routine_id: uuid.UUID, body: RoutinePatch, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    return _out(await RoutineService(session).update(user.id, routine_id, **changes))


@router.delete("/{routine_id}", status_code=204)
async def delete_routine(routine_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> None:
    await RoutineService(session).delete(user.id, routine_id)


@router.post("/{routine_id}/run", status_code=202)
async def run_now(routine_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    service = RoutineService(session)
    job_id = await service.fire(await service.get(user.id, routine_id))
    return {"job_id": str(job_id) if job_id else None}
