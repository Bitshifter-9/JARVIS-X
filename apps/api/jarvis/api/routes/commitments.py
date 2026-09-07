"""Commitment tracking: the promises you made (second-brain #22)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.errors import NotFound
from jarvis.db.models.domain import Commitment
from jarvis.services.commitment import scan_commitments
from jarvis.services.commitment.service import commitment_out

router = APIRouter(prefix="/v1/commitments", tags=["commitments"])


@router.get("")
async def list_commitments(
    user: CurrentUser, session: SessionDep, status: str = "open"
) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(Commitment)
            .where(Commitment.user_id == user.id, Commitment.status == status)
            .order_by(Commitment.due_at.asc().nulls_last(), Commitment.created_at.desc())
        )
    ).all()
    return [commitment_out(c) for c in rows]


@router.post("/scan")
async def scan(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    caught = await scan_commitments(session, user.id)
    await session.commit()
    return {"ok": True, "caught": caught}


async def _owned(session, user_id: uuid.UUID, commitment_id: uuid.UUID) -> Commitment:  # noqa: ANN001
    c = await session.get(Commitment, commitment_id)
    if c is None or c.user_id != user_id:
        raise NotFound("Commitment")
    return c


@router.post("/{commitment_id}/done")
async def mark_done(
    commitment_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    c = await _owned(session, user.id, commitment_id)
    c.status = "done"
    c.completed_at = datetime.now(UTC)
    await session.flush()
    return commitment_out(c)


@router.post("/{commitment_id}/drop")
async def drop(
    commitment_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    c = await _owned(session, user.id, commitment_id)
    c.status = "dropped"
    await session.flush()
    return commitment_out(c)
