"""Knowledge-gap detector (#29): topics you keep asking about — a sign you never quite
learned them, and a candidate for a micro-lesson. Deterministic: the recurring subjects of
the *questions* in your own messages. No model.
"""

from __future__ import annotations

import uuid
from typing import Any

from jarvis.db.models.chat import ChatMessage
from jarvis.services.phrasebook import extract_terms
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_QUESTION_STARTS = ("how ", "what ", "why ", "when ", "where ", "which ", "who ", "can i ",
                    "should i", "is there", "explain", "help me")


def _is_question(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.endswith("?") or t.startswith(_QUESTION_STARTS)


async def knowledge_gaps(
    session: AsyncSession, user_id: uuid.UUID, *, sample: int = 800, limit: int = 8
) -> list[dict[str, Any]]:
    """Subjects that recur across the questions you've asked — likely gaps worth a lesson."""
    texts = list(
        (
            await session.scalars(
                select(ChatMessage.content)
                .where(ChatMessage.user_id == user_id, ChatMessage.role == "user")
                .order_by(ChatMessage.created_at.desc())
                .limit(sample)
            )
        ).all()
    )
    questions = [t for t in texts if _is_question(t)]
    if len(questions) < 3:
        return []
    # The recurring proper-noun/topic terms *within the questions* are the gap candidates.
    return [
        {"topic": t["term"], "asked": t["count"]}
        for t in extract_terms(questions, min_count=2, limit=limit)
    ]


if __name__ == "__main__":  # self-check
    assert _is_question("How do I set up Kubernetes?")
    assert _is_question("what is the plan")
    assert not _is_question("Remind me to email Sam")
    print("ok")
