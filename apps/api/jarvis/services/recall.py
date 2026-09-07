"""Instant contextual recall (#23): ask your life a question, get a sourced answer.

Retrieval is the free keyword tier (``life_search`` over mail, deadlines, chat and memory);
synthesis runs through the LLM cascade, which prefers free/local providers and only reaches
a paid one because *you* asked (never in the background). If no model answers, it degrades to
the ranked sources — a useful result on its own. Semantic re-ranking over the memory
embeddings is the later optimisation the roadmap notes.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from jarvis.core.logging import get_logger
from jarvis.services.search import life_search
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

# Question words and filler that carry no search signal.
_STOP = {
    "what", "when", "where", "which", "whom", "whose", "that", "this", "with", "about",
    "did", "does", "do", "was", "were", "have", "has", "had", "the", "and", "for", "you",
    "your", "our", "are", "will", "would", "should", "could", "from", "into", "any", "all",
    "how", "why", "who", "there", "their", "they", "them", "then", "than", "over", "some",
    "tell", "show", "find", "give", "get", "say", "said", "decide", "decided", "remember",
}


def _keywords(question: str) -> list[str]:
    """The salient words to search for — a natural-language question won't LIKE-match as one
    string, so we search per keyword and merge."""
    words = re.findall(r"[A-Za-z0-9']{3,}", question.lower())
    seen: list[str] = []
    for w in words:
        if w not in _STOP and w not in seen:
            seen.append(w)
    return seen[:6]

_SYSTEM = (
    "You answer the user's question about their own life using ONLY the numbered sources. "
    "Cite every claim with its number like [1]. Be concise — two or three sentences. If the "
    "sources do not contain the answer, say you don't have that yet; never invent one."
)


async def ask_life(
    session: AsyncSession,
    user_id: uuid.UUID,
    question: str,
    *,
    router: Any = None,
    limit: int = 6,
) -> dict[str, Any]:
    q = (question or "").strip()
    if len(q) < 3:
        return {"answer": None, "sources": [], "grounded": False}

    # A question isn't a keyword — search each salient word and merge, ranking a hit by how
    # many of the question's words it matched (then by the search's own recency order).
    terms = _keywords(q) or [q]
    merged: dict[str, dict[str, Any]] = {}
    hits_terms: dict[str, int] = {}
    for term in terms:
        for h in await life_search(session, user_id, term, limit=limit):
            key = str(h.get("id") or h.get("title"))
            merged.setdefault(key, h)
            hits_terms[key] = hits_terms.get(key, 0) + 1

    ranked = sorted(merged.values(), key=lambda h: hits_terms[str(h.get("id") or h.get("title"))],
                    reverse=True)
    sources = [
        {
            "n": i + 1,
            "type": h["type"],
            "title": h["title"],
            "snippet": h.get("snippet") or "",
            "who": h.get("who"),
            "when": h.get("when"),
            "route": h.get("route"),
            "id": h.get("id"),
        }
        for i, h in enumerate(ranked[:limit])
    ]
    if not sources:
        return {"answer": None, "sources": [], "grounded": False}

    context = "\n".join(
        f"[{s['n']}] ({s['type']}"
        + (f", {s['who']}" if s['who'] else "")
        + f") {s['title']}: {s['snippet']}"
        for s in sources
    )

    answer: str | None = None
    try:
        from jarvis.llm.router import LLMRouter
        from jarvis.llm.types import Message

        r = router or LLMRouter(session)
        resp = await r.chat(
            [
                Message("system", _SYSTEM),
                Message("user", f"Question: {q}\n\nSources:\n{context}"),
            ],
            user_id=user_id,
            max_tokens=400,
        )
        answer = (resp.text or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — no model? the sources are still the answer
        log.info("recall_unsynthesised", error=str(exc)[:160])
        answer = None

    return {"answer": answer, "sources": sources, "grounded": True}
