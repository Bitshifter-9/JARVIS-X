"""Private mood trend (#18): a gentle sentiment line from your own words, on your account
only — never shared. Deterministic lexicon scoring (no model, nothing leaves your data), by
week, so the coach can notice a rough patch without anyone reading your journal.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.db.models.chat import ChatMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_POS = {
    "good", "great", "happy", "glad", "excited", "love", "love it", "grateful", "thanks",
    "thank", "awesome", "amazing", "wonderful", "proud", "win", "won", "progress", "done",
    "better", "calm", "relaxed", "hopeful", "confident", "enjoy", "enjoyed", "fun", "nice",
    "excellent", "perfect", "yay", "relief", "relieved", "energised", "energized", "motivated",
}
_NEG = {
    "bad", "sad", "angry", "upset", "tired", "exhausted", "stressed", "stress", "anxious",
    "anxiety", "worried", "worry", "afraid", "fear", "frustrated", "frustrating", "annoyed",
    "hate", "hate it", "overwhelmed", "burnt", "burnout", "lonely", "depressed", "down",
    "sick", "pain", "hurts", "struggle", "struggling", "fail", "failed", "failure", "lost",
    "confused", "stuck", "hopeless", "can't", "cannot", "problem", "issue", "sorry",
}
_NEGATORS = {"not", "no", "never", "hardly", "barely", "isnt", "isn't", "dont", "don't",
             "cant", "can't", "wasnt", "wasn't"}
_WORD = re.compile(r"[a-z']+")


def score_text(text: str) -> tuple[int, int]:
    """(polarity_sum, hits) for one message — a preceding negator flips a word's sign."""
    words = _WORD.findall((text or "").lower())
    pol, hits = 0, 0
    for i, w in enumerate(words):
        s = 1 if w in _POS else (-1 if w in _NEG else 0)
        if s == 0:
            continue
        if i > 0 and words[i - 1] in _NEGATORS:
            s = -s
        pol += s
        hits += 1
    return pol, hits


async def mood_trend(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    weeks: int = 12,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A per-week sentiment score in [-1, 1] over the user's own messages. Weeks with no
    emotional words are omitted; too little signal returns ``enough_data=False``."""
    moment = now or datetime.now(UTC)
    since = moment - timedelta(weeks=weeks)
    rows = (
        await session.execute(
            select(ChatMessage.content, ChatMessage.created_at).where(
                ChatMessage.user_id == user_id,
                ChatMessage.role == "user",
                ChatMessage.created_at >= since,
            )
        )
    ).all()

    buckets: dict[str, list[int]] = {}
    total_hits = 0
    for content, at in rows:
        if not at:
            continue
        pol, hits = score_text(content)
        if hits == 0:
            continue
        total_hits += hits
        # ISO week start (Monday) as the bucket key.
        monday = (at - timedelta(days=at.weekday())).date().isoformat()
        b = buckets.setdefault(monday, [0, 0])
        b[0] += pol
        b[1] += hits

    if total_hits < 6:
        return {"enough_data": False, "points": []}

    points = [
        {"week": wk, "score": round(pol / hits, 3), "samples": hits}
        for wk, (pol, hits) in sorted(buckets.items())
    ]
    latest = points[-1]["score"]
    return {
        "enough_data": True,
        "points": points,
        "latest": latest,
        "mood": "up" if latest > 0.15 else ("down" if latest < -0.15 else "steady"),
    }


if __name__ == "__main__":  # self-check of the scorer
    assert score_text("I feel great and happy today")[0] == 2
    assert score_text("I am so stressed and tired")[0] == -2
    assert score_text("not happy at all")[0] == -1  # negator flips
    assert score_text("the meeting is at three")[1] == 0  # no emotional words
    print("ok")
