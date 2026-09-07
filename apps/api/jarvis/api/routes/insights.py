"""Mail intelligence: labels, spending, travel, and the away digest (FEATURES-50)."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.db.models.source import MailInsight
from jarvis.services.insights import (
    anomaly_nudges,
    away_digest,
    derive_insights,
    grouped_activity,
)

router = APIRouter(prefix="/v1/insights", tags=["insights"])


@router.post("/scan")
async def scan(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    written = await derive_insights(session, user.id)
    await session.commit()
    return {"ok": True, "new": written}


@router.get("/labels")
async def labels(user: CurrentUser, session: SessionDep) -> dict[str, int]:
    rows = (
        await session.scalars(
            select(MailInsight.label).where(MailInsight.user_id == user.id)
        )
    ).all()
    counts: dict[str, int] = defaultdict(int)
    for label in rows:
        counts[label] += 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


@router.get("/spending")
async def spending(
    user: CurrentUser,
    session: SessionDep,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await session.scalars(
            select(MailInsight)
            .where(
                MailInsight.user_id == user.id,
                MailInsight.amount.is_not(None),
                MailInsight.occurred_at >= since,
            )
            .order_by(MailInsight.occurred_at.desc())
        )
    ).all()
    totals: dict[str, float] = defaultdict(float)
    items = []
    for r in rows:
        totals[r.currency or "?"] += r.amount or 0.0
        items.append(
            {
                "merchant": r.merchant,
                "amount": r.amount,
                "currency": r.currency,
                "is_bill": r.is_bill,
                "when": r.occurred_at.isoformat() if r.occurred_at else None,
            }
        )
    return {
        "days": days,
        "totals": {k: round(v, 2) for k, v in totals.items()},
        "bills_due": [i for i in items if i["is_bill"]],
        "items": items,
    }


@router.get("/travel")
async def travel(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(MailInsight)
            .where(MailInsight.user_id == user.id, MailInsight.travel.is_not(None))
            .order_by(MailInsight.occurred_at.desc())
            .limit(50)
        )
    ).all()
    return [{**(r.travel or {}), "merchant": r.merchant} for r in rows]


@router.get("/grouped")
async def grouped(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    return await grouped_activity(session, user.id)


@router.get("/anomalies")
async def anomalies(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    return await anomaly_nudges(session, user.id)


@router.get("/away")
async def away(
    user: CurrentUser,
    session: SessionDep,
    hours: int = Query(default=24, ge=1, le=720),
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    return await away_digest(session, user.id, since=since)
