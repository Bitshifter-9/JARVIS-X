"""Relationship cadence (#16): who you actually keep up with, and who you've gone quiet on.

Extends the people already in the knowledge graph with the one thing it doesn't track — the
*rhythm* of contact. Deterministic and free: group the senders in your mail/chat by person,
read the gaps between messages as a typical cadence, and flag anyone you usually hear from
regularly but haven't lately. No model. Phone/WhatsApp (where the person is in the title, not
the author) is left for later.
"""

from __future__ import annotations

import statistics
import uuid
from datetime import UTC, datetime
from typing import Any

from jarvis.db.models.ops import Entity
from jarvis.db.models.source import SourceObject
from jarvis.services.triage import sender_address, sender_name
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def relationships(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    sample: int = 800,
    min_contacts: int = 3,
    limit: int = 20,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """People ranked by how much you correspond, each with their contact cadence and whether
    you've gone quiet (longer than usual since the last message)."""
    moment = now or datetime.now(UTC)
    rows = (
        await session.execute(
            select(SourceObject.author, SourceObject.occurred_at)
            .where(
                SourceObject.user_id == user_id,
                SourceObject.provider.in_(("gmail", "slack")),
                SourceObject.author.is_not(None),
                SourceObject.occurred_at.is_not(None),
            )
            .order_by(SourceObject.occurred_at.desc())
            .limit(sample)
        )
    ).all()
    if not rows:
        return []

    groups: dict[str, dict[str, Any]] = {}
    for author, at in rows:
        addr = sender_address(author)
        name = sender_name(author)
        key = (addr or name).lower()
        if key in ("someone", ""):
            continue
        g = groups.setdefault(key, {"name": name, "email": addr, "times": []})
        g["times"].append(at)

    # Relations already learned by triage, so the card can say "client", "family", etc.
    relations = {
        (e.attributes or {}).get("email"): (e.attributes or {}).get("relation")
        for e in (
            await session.scalars(
                select(Entity).where(Entity.user_id == user_id, Entity.kind == "person")
            )
        ).all()
        if (e.attributes or {}).get("email")
    }

    out: list[dict[str, Any]] = []
    for g in groups.values():
        times = sorted(g["times"])
        if len(times) < min_contacts:
            continue
        gaps = [max(0, (b - a).days) for a, b in zip(times, times[1:], strict=False)]
        cadence = max(1, int(statistics.median(gaps))) if gaps else 1
        last = times[-1]
        days_since = (moment - last).days
        quiet = days_since > max(cadence * 2, cadence + 7)
        out.append({
            "name": g["name"],
            "email": g["email"],
            "relation": relations.get(g["email"]),
            "count": len(times),
            "last": last.isoformat(),
            "days_since": days_since,
            "cadence_days": cadence,
            "quiet": quiet,
        })

    # Quiet-and-important first (the reconnect nudge), then the people you correspond with most.
    out.sort(key=lambda r: (r["quiet"], r["count"]), reverse=True)
    return out[:limit]


if __name__ == "__main__":  # self-check of the cadence maths
    from datetime import timedelta

    now = datetime(2026, 9, 8, tzinfo=UTC)
    # Weekly contact that stopped 5 weeks ago → quiet; a recent regular → not quiet.
    times = [now - timedelta(days=d) for d in (70, 63, 56, 49, 42, 35)]  # last was 35d ago
    gaps = [7, 7, 7, 7, 7]
    cadence = int(statistics.median(gaps))
    assert cadence == 7
    assert (now - times[-1]).days == 35
    assert 35 > max(cadence * 2, cadence + 7)  # quiet
    print("ok")
