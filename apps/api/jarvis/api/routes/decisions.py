"""Decision journal + outcome review (#39)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.errors import NotFound
from jarvis.services import decisions as svc

router = APIRouter(prefix="/v1/decisions", tags=["decisions"])


class DecisionIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    reasoning: str | None = Field(default=None, max_length=4000)
    expected: str | None = Field(default=None, max_length=2000)
    review_in_days: int = Field(default=30, ge=1, le=365)


class ReviewIn(BaseModel):
    outcome: str = Field(pattern="^(worked|mixed|didnt)$")
    note: str | None = Field(default=None, max_length=2000)


@router.post("", status_code=201)
async def log(body: DecisionIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    d = await svc.log_decision(session, user.id, text=body.text, reasoning=body.reasoning,
                               expected=body.expected, review_in_days=body.review_in_days)
    await session.commit()
    return svc.decision_out(d)


@router.get("")
async def listing(
    user: CurrentUser, session: SessionDep, status: str | None = None
) -> list[dict[str, Any]]:
    return [svc.decision_out(d) for d in await svc.list_decisions(session, user.id, status=status)]


@router.get("/due")
async def due(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    """Decisions whose review date has arrived — 'did it work?'."""
    return [svc.decision_out(d) for d in await svc.due_for_review(session, user.id)]


@router.post("/{decision_id}/review")
async def review(
    decision_id: uuid.UUID, body: ReviewIn, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    d = await svc.record_outcome(
        session, user.id, decision_id, outcome=body.outcome, note=body.note
    )
    if d is None:
        raise NotFound("Decision")
    await session.commit()
    return svc.decision_out(d)
