"""Personal phrasebook (#13): the recurring names, jargon and acronyms in the user's own
words — so the transcriber and drafter stop mishearing "Guru Vai" as "guruvhy".

Deterministic and free: proper-noun and acronym frequency over the user's own text (their
chat messages and the profile they wrote). No model. The transcriber/drafter integration is
later work; this is the vocabulary those will draw on.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from typing import Any

from jarvis.db.models.chat import ChatMessage
from jarvis.db.models.ops import Profile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Capitalised words that start sentences or are just common words — never jargon.
_COMMON = {
    "the", "a", "an", "and", "but", "or", "so", "if", "then", "this", "that", "these",
    "those", "i", "you", "we", "he", "she", "it", "they", "my", "your", "our", "his",
    "her", "their", "me", "him", "them", "is", "are", "was", "were", "be", "been", "do",
    "does", "did", "have", "has", "had", "will", "would", "can", "could", "should", "may",
    "might", "must", "for", "with", "from", "into", "at", "on", "in", "of", "to", "by",
    "as", "not", "no", "yes", "ok", "okay", "hi", "hey", "hello", "thanks", "thank",
    "please", "what", "when", "where", "why", "how", "who", "which", "there", "here",
    "now", "today", "tomorrow", "yesterday", "let", "get", "got", "make", "made", "also",
    "just", "like", "want", "need", "know", "think", "see", "good", "great", "sure",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december", "jarvis",
}

# A run of Capitalised words (a proper name/phrase); and single Capitalised tokens/acronyms.
_PHRASE = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})\b")
_TOKEN = re.compile(r"\b([A-Z][A-Za-z]+)\b")  # "Priya", "API" — 2+ chars, starts uppercase


def _lead_stripped(words: list[str]) -> list[str]:
    # Drop leading sentence-start / common words ("The Guru Vai" → "Guru Vai", "Ping" stays).
    while words and words[0].lower() in _COMMON:
        words = words[1:]
    return words


def extract_terms(texts: list[str], *, min_count: int = 2, limit: int = 40) -> list[dict[str, Any]]:
    """Recurring proper phrases, names and acronyms across ``texts``. Phrases and single
    proper nouns are counted separately (so a name isn't swallowed by an adjacent
    sentence-start word), a term must recur (``min_count``), and a single word already
    covered by a more-frequent phrase is dropped as redundant. Casing is the most common form.
    """
    phrase_n: Counter[str] = Counter()
    phrase_case: dict[str, Counter[str]] = {}
    token_n: Counter[str] = Counter()
    token_case: dict[str, Counter[str]] = {}

    for text in texts:
        t = text or ""
        for m in _PHRASE.finditer(t):
            words = _lead_stripped(m.group(1).split())
            if len(words) >= 2:
                term = " ".join(words)
                phrase_n[term.lower()] += 1
                phrase_case.setdefault(term.lower(), Counter())[term] += 1
        for m in _TOKEN.finditer(t):
            tok = m.group(1)
            if tok.lower() in _COMMON:
                continue
            token_n[tok.lower()] += 1
            token_case.setdefault(tok.lower(), Counter())[tok] += 1

    # How often each word appears inside a kept phrase — used to drop redundant singletons.
    covered: dict[str, int] = {}
    kept_phrases = [(k, n) for k, n in phrase_n.items() if n >= min_count]
    for key, n in kept_phrases:
        for w in key.split():
            covered[w] = max(covered.get(w, 0), n)

    results: list[tuple[str, int]] = [
        (phrase_case[k].most_common(1)[0][0], n) for k, n in kept_phrases
    ]
    for key, n in token_n.items():
        if n < min_count or covered.get(key, 0) >= n:
            continue
        results.append((token_case[key].most_common(1)[0][0], n))

    results.sort(key=lambda r: (r[1], len(r[0])), reverse=True)
    return [{"term": t, "count": n} for t, n in results[:limit]]


async def phrasebook(
    session: AsyncSession, user_id: uuid.UUID, *, sample: int = 400, limit: int = 40
) -> list[dict[str, Any]]:
    """The user's recurring vocabulary from their own words (chat + the profile they wrote)."""
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
    profile = await session.get(Profile, user_id)
    if profile:
        texts += [profile.about, profile.people, profile.priorities, profile.decisions]
    return extract_terms([t for t in texts if t], limit=limit)


if __name__ == "__main__":  # self-check of the extractor
    msgs = [
        "Remind me to email Guru Vai Sciences about the API deadline.",
        "The Guru Vai Sciences invoice is due. Ping Priya on the API side.",
        "Guru Vai Sciences again — and the API keys for Priya.",
        "The weather is nice and I feel great today.",
    ]
    terms = {t["term"] for t in extract_terms(msgs)}
    assert "Guru Vai Sciences" in terms, terms
    assert "API" in terms, terms
    assert "Priya" in terms, terms
    assert "The" not in terms and "Remind" not in terms, terms
    print("ok", sorted(terms))
