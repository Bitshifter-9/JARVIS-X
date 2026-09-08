"""Personalised micro-lessons (#35): turn a gap you keep hitting (#29) into a crisp 3-minute
lesson — a few points and one action — through the free/local cascade. A paid call fires only
because you asked; degrades to "can't right now" if no model answers.
"""

from __future__ import annotations

import uuid
from typing import Any

from jarvis.core.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

_SYSTEM = (
    "You are a concise tutor. Teach the topic as a 3-minute micro-lesson: 3-4 crisp bullet "
    "points that build understanding, then one small action to try. No preamble, no fluff. "
    "Plain text."
)


async def micro_lesson(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    topic: str | None = None,
    router: Any = None,
) -> dict[str, Any]:
    """A short lesson for a topic (or your top knowledge gap if none is given)."""
    if not topic:
        from jarvis.services.knowledge_gaps import knowledge_gaps

        gaps = await knowledge_gaps(session, user_id, limit=1)
        if not gaps:
            return {}
        topic = gaps[0]["topic"]

    try:
        from jarvis.llm.router import LLMRouter
        from jarvis.llm.types import Message

        r = router or LLMRouter(session)
        resp = await r.chat(
            [Message("system", _SYSTEM), Message("user", f"Teach me: {topic}")],
            user_id=user_id,
            max_tokens=500,
        )
        lesson = (resp.text or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — no model reachable
        log.info("micro_lesson_unavailable", error=str(exc)[:160])
        return {"topic": topic, "lesson": None,
                "reason": "The lesson model isn't reachable right now."}
    return {"topic": topic, "lesson": lesson}
