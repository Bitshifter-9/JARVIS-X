"""Speech-pattern profile (#12): how you actually phrase things — the filler words you lean
on, the phrases you repeat, your typical sentence length — from your own messages. Feeds
drafting-in-your-voice and the communication coach. Deterministic, no model.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from typing import Any

from jarvis.db.models.chat import ChatMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Multi-word fillers checked first, then single-word.
_FILLERS = [
    "you know", "i mean", "sort of", "kind of", "i guess", "or something", "to be honest",
    "at the end of the day", "um", "uh", "like", "actually", "basically", "literally",
    "honestly", "obviously", "just", "really", "maybe", "probably",
]
_STOP = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with", "is",
    "are", "was", "it", "this", "that", "i", "you", "we", "my", "me", "so", "at", "be",
    "do", "can", "will", "if", "as", "by", "he", "she", "they", "have", "has",
}
_WORD = re.compile(r"[a-z']+")
_SENT = re.compile(r"[.!?]+")


async def speech_profile(
    session: AsyncSession, user_id: uuid.UUID, *, sample: int = 500
) -> dict[str, Any]:
    """Filler words, favourite phrases, and sentence length from the user's own messages."""
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
    if len(texts) < 5:
        return {"enough_data": False}

    filler_counts: Counter[str] = Counter()
    bigrams: Counter[str] = Counter()
    sentence_words: list[int] = []
    total_words = 0

    for text in texts:
        low = (text or "").lower()
        for f in _FILLERS:
            n = len(re.findall(rf"\b{re.escape(f)}\b", low))
            if n:
                filler_counts[f] += n
        for part in _SENT.split(text or ""):
            words = _WORD.findall(part.lower())
            if words:
                sentence_words.append(len(words))
                total_words += len(words)
        words = _WORD.findall(low)
        for a, b in zip(words, words[1:], strict=False):
            if a not in _STOP and b not in _STOP:
                bigrams[f"{a} {b}"] += 1

    avg_sentence = round(sum(sentence_words) / len(sentence_words), 1) if sentence_words else 0
    return {
        "enough_data": True,
        "messages": len(texts),
        "avg_sentence_words": avg_sentence,
        "fillers": [{"term": f, "count": n} for f, n in filler_counts.most_common(6)],
        "favourite_phrases": [
            {"term": p, "count": n} for p, n in bigrams.most_common(8) if n >= 3
        ],
    }


if __name__ == "__main__":  # self-check
    import re as _re
    low = "you know i really just think, honestly, it works"
    assert len(_re.findall(r"\byou know\b", low)) == 1
    print("ok")
