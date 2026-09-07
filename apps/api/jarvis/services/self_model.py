"""Self-model export (#50): a single portable bundle of everything JARVIS has learned to be
a model of you — the style you write in, the words you use, your rhythm, your relationships,
your open promises and the decisions you've graded. Versioned, curated (not a raw dump — that
is ``/v1/export``), and the thing a future embodied agent could load to *be* you.

Composes the deterministic learners already built. No model, no secrets — credentials and
key material never enter it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from jarvis.db.models.domain import Commitment, Decision
from jarvis.db.models.ops import Memory, Profile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

SELF_MODEL_VERSION = "1.0"


async def build_self_model(
    session: AsyncSession, user_id: uuid.UUID, *, tz: str = "UTC"
) -> dict[str, Any]:
    from jarvis.services.interests import interest_drift
    from jarvis.services.phrasebook import phrasebook
    from jarvis.services.relationships import relationships
    from jarvis.services.rhythm import rhythm
    from jarvis.services.speech import speech_profile

    profile = await session.get(Profile, user_id)
    persona = {
        k: getattr(profile, k)
        for k in ("about", "priorities", "people", "style", "learned_style", "decisions")
    } if profile else {}

    memories = (
        await session.scalars(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.invalidated_at.is_(None),
                Memory.kind.in_(("semantic", "source")),
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(200)
        )
    ).all()

    commitments = (
        await session.scalars(
            select(Commitment).where(
                Commitment.user_id == user_id, Commitment.status == "open"
            )
        )
    ).all()
    decisions = (
        await session.scalars(
            select(Decision).where(
                Decision.user_id == user_id, Decision.status == "reviewed"
            )
        )
    ).all()

    return {
        "version": SELF_MODEL_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "model": {
            "persona": persona,
            "phrasebook": await phrasebook(session, user_id),
            "speech": await speech_profile(session, user_id),
            "rhythm": await rhythm(session, user_id, tz=tz),
            "interests": await interest_drift(session, user_id),
            "relationships": [
                {"name": r["name"], "relation": r.get("relation"),
                 "cadence_days": r["cadence_days"]}
                for r in await relationships(session, user_id, limit=40)
            ],
            "knowledge": [
                {"content": m.content, "kind": m.kind, "importance": m.importance}
                for m in memories
            ],
            "open_commitments": [c.text for c in commitments],
            "decisions_reviewed": [
                {"text": d.text, "reasoning": d.reasoning, "outcome": d.outcome}
                for d in decisions
            ],
        },
        "counts": {
            "knowledge": len(memories),
            "commitments": len(commitments),
            "decisions": len(decisions),
        },
    }
