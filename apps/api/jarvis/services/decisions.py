"""Decision journal + outcome review (#39): log a decision and your reasoning; weeks later,
"did it work?" — so you learn to decide better. The review resurfaces once its date passes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.db.models.domain import Decision
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_OUTCOMES = ("worked", "mixed", "didnt")


def decision_out(d: Decision) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "text": d.text,
        "reasoning": d.reasoning,
        "expected": d.expected,
        "review_at": d.review_at.isoformat() if d.review_at else None,
        "status": d.status,
        "outcome": d.outcome,
        "outcome_note": d.outcome_note,
        "created_at": d.created_at.isoformat(),
    }


async def log_decision(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    text: str,
    reasoning: str | None = None,
    expected: str | None = None,
    review_in_days: int = 30,
) -> Decision:
    review_at = datetime.now(UTC) + timedelta(days=max(1, review_in_days))
    d = Decision(user_id=user_id, text=text[:2000], reasoning=reasoning,
                 expected=expected, review_at=review_at)
    session.add(d)
    await session.flush()
    return d


async def list_decisions(
    session: AsyncSession, user_id: uuid.UUID, *, status: str | None = None
) -> list[Decision]:
    q = select(Decision).where(Decision.user_id == user_id)
    if status:
        q = q.where(Decision.status == status)
    q = q.order_by(Decision.created_at.desc())
    return list((await session.scalars(q)).all())


async def due_for_review(session: AsyncSession, user_id: uuid.UUID) -> list[Decision]:
    """Open decisions whose review date has arrived — time to ask 'did it work?'."""
    now = datetime.now(UTC)
    return list(
        (
            await session.scalars(
                select(Decision)
                .where(
                    Decision.user_id == user_id,
                    Decision.status == "open",
                    Decision.review_at.is_not(None),
                    Decision.review_at <= now,
                )
                .order_by(Decision.review_at.asc())
            )
        ).all()
    )


async def record_outcome(
    session: AsyncSession,
    user_id: uuid.UUID,
    decision_id: uuid.UUID,
    *,
    outcome: str,
    note: str | None = None,
) -> Decision | None:
    if outcome not in _OUTCOMES:
        raise ValueError(f"outcome must be one of {_OUTCOMES}")
    d = await session.get(Decision, decision_id)
    if d is None or d.user_id != user_id:
        return None
    d.outcome = outcome
    d.outcome_note = note
    d.status = "reviewed"
    d.reviewed_at = datetime.now(UTC)
    await session.flush()
    return d
