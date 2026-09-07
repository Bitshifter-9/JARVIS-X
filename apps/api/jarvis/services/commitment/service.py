"""Find the promises a person makes in their own words, and track them to done.

Keyword-only detection (first-person commitment cues) + the regex deadline reader for the
date — free, no model. The point is: "I'll send it Friday" shouldn't evaporate.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from jarvis.db.models.chat import ChatMessage
from jarvis.db.models.domain import Commitment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# First-person promises. "you should" / "can you" are requests to others, not the user's
# own commitment, so they are deliberately excluded.
_CUE = re.compile(
    r"\b("
    r"i['’]?ll|i will|i['’]?m going to|i am going to|"
    r"i need to|i have to|i've got to|i gotta|i must|"
    r"i promise(?: to)?|let me|remind me to|i'll get back to"
    r")\b",
    re.IGNORECASE,
)


def detect_commitment(text: str) -> str | None:
    """The first sentence in ``text`` that reads as a promise the speaker made, or None."""
    for raw in re.split(r"[.!?\n]+", text):
        s = raw.strip()
        if 4 <= len(s) <= 200 and _CUE.search(s):
            return s
    return None


def _dedupe_key(user_id: uuid.UUID, sentence: str) -> str:
    norm = re.sub(r"\s+", " ", sentence.strip().lower())
    return hashlib.sha256(f"{user_id}:{norm}".encode()).hexdigest()[:64]


async def scan_commitments(session: AsyncSession, user_id: uuid.UUID, *, limit: int = 100) -> int:
    """Read the user's recent chat messages, catch new commitments, store them. Returns how
    many new ones were caught. Idempotent on the sentence (dedupe_key)."""
    from jarvis.core.config import get_settings
    from jarvis.services.extraction.regex_fallback import extract_deadline
    from jarvis.services.extraction.resolver import resolve

    rows = (
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.user_id == user_id, ChatMessage.role == "user")
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
    ).all()

    seen = set(
        (
            await session.scalars(
                select(Commitment.dedupe_key).where(Commitment.user_id == user_id)
            )
        ).all()
    )
    tz = get_settings().timezone
    caught = 0
    for msg in rows:
        sentence = detect_commitment(msg.content)
        if sentence is None:
            continue
        key = _dedupe_key(user_id, sentence)
        if key in seen:
            continue
        seen.add(key)
        due = None
        guessed = extract_deadline(sentence, sentence, msg.created_at or datetime.now(UTC),
                                   require_cue=False)
        if guessed is not None and guessed.has_deadline:
            try:
                resolved = resolve(guessed, received_at=msg.created_at or datetime.now(UTC),
                                   default_timezone=tz)
                due = resolved.due_at if resolved else None
            except Exception:  # noqa: BLE001
                due = None
        session.add(
            Commitment(user_id=user_id, text=sentence[:500], due_at=due,
                       source="chat", dedupe_key=key)
        )
        caught += 1
    await session.flush()
    return caught


async def claim_due_commitments(
    session: AsyncSession, *, within_hours: int = 6
) -> list[Commitment]:
    """About-to-forget nudge (second-brain #24): open, dated commitments coming due within
    the window that haven't been reminded yet — claimed (reminded_at set) so the caller can
    push exactly once. Returns the ones to nudge."""
    from datetime import timedelta

    now = datetime.now(UTC)
    horizon = now + timedelta(hours=within_hours)
    rows = (
        await session.scalars(
            select(Commitment).where(
                Commitment.status == "open",
                Commitment.reminded_at.is_(None),
                Commitment.due_at.is_not(None),
                Commitment.due_at <= horizon,
            )
        )
    ).all()
    for c in rows:
        c.reminded_at = now
    await session.flush()
    return list(rows)


def commitment_out(c: Commitment) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "text": c.text,
        "due": c.due_at.isoformat() if c.due_at else None,
        "source": c.source,
        "status": c.status,
        "created_at": c.created_at.isoformat(),
    }
