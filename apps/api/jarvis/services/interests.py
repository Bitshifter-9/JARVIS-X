"""Interest drift (#17): what you're into now versus what you're drifting from.

Deterministic and free: the proper nouns and topics in your *own* recent messages against a
longer baseline — what's newly prominent (rising) and what has dropped off (fading). Reuses
the phrasebook extractor. A stale-profile briefing tracks yesterday's you; this tracks the
current one. Richer signal will come once reading/watching capture (#4) lands.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.db.models.chat import ChatMessage
from jarvis.services.phrasebook import extract_terms
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def interest_drift(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    recent_days: int = 30,
    baseline_days: int = 120,
    limit: int = 8,
    now: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Topics rising and fading in the user's own words, recent window vs the baseline."""
    moment = now or datetime.now(UTC)
    recent_cut = moment - timedelta(days=recent_days)
    base_cut = moment - timedelta(days=baseline_days)

    rows = (
        await session.execute(
            select(ChatMessage.content, ChatMessage.created_at)
            .where(
                ChatMessage.user_id == user_id,
                ChatMessage.role == "user",
                ChatMessage.created_at >= base_cut,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(2000)
        )
    ).all()

    recent_texts = [c for c, t in rows if t and t >= recent_cut]
    base_texts = [c for c, t in rows if t and t < recent_cut]

    recent = {t["term"]: t["count"] for t in extract_terms(recent_texts, limit=100)}
    base = {t["term"]: t["count"] for t in extract_terms(base_texts, limit=100)}

    rising = [
        {"term": term, "recent": rc, "baseline": base.get(term, 0)}
        for term, rc in recent.items()
        if rc > base.get(term, 0)
    ]
    fading = [
        {"term": term, "recent": recent.get(term, 0), "baseline": bc}
        for term, bc in base.items()
        if recent.get(term, 0) == 0
    ]
    rising.sort(key=lambda x: (x["recent"] - x["baseline"], x["recent"]), reverse=True)
    fading.sort(key=lambda x: x["baseline"], reverse=True)
    return {"rising": rising[:limit], "fading": fading[:limit]}


if __name__ == "__main__":  # self-check of the drift split
    # Simulate the two windows directly through extract_terms to check the split logic.
    recent = {t["term"]: t["count"] for t in extract_terms(
        ["Kubernetes rollout again.", "The Kubernetes upgrade.", "More Kubernetes today."])}
    base = {t["term"]: t["count"] for t in extract_terms(
        ["Photography trip planning.", "Photography gear.", "Photography weekend."])}
    rising = [t for t, rc in recent.items() if rc > base.get(t, 0)]
    fading = [t for t, bc in base.items() if recent.get(t, 0) == 0]
    assert "Kubernetes" in rising, rising
    assert "Photography" in fading, fading
    print("ok")
