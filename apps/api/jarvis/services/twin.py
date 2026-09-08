"""Digital twin persona (#19): a persona that answers *as you* — for drafting, rehearsing a
hard conversation, or "what would I say?". Grounded on the self-model already learned (the
profile you wrote, your style card, your phrasebook and speech pattern), answered through the
free/local cascade. The seed of the portable self-model. Degrades to "not enough of you yet"
if there's no model or too little signal.
"""

from __future__ import annotations

import uuid
from typing import Any

from jarvis.core.logging import get_logger
from jarvis.db.models.ops import Profile
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

_SYSTEM = (
    "You are answering AS this specific person, in the first person, in their own voice — not "
    "as an assistant. Use how they write and the words they use. Be concise. If the profile "
    "below is too thin to answer as them, say you don't know them well enough yet rather than "
    "inventing a personality."
)


async def _context(session: AsyncSession, user_id: uuid.UUID) -> str:
    from jarvis.services.phrasebook import phrasebook
    from jarvis.services.speech import speech_profile

    profile = await session.get(Profile, user_id)
    parts: list[str] = []
    if profile:
        for label, key in (
            ("About them", "about"), ("Priorities", "priorities"),
            ("People", "people"), ("How they write", "learned_style"),
        ):
            val = (getattr(profile, key, "") or "").strip()
            if val:
                parts.append(f"{label}: {val[:600]}")
    terms = [t["term"] for t in await phrasebook(session, user_id)][:12]
    if terms:
        parts.append("Words/names they use: " + ", ".join(terms))
    speech = await speech_profile(session, user_id)
    if speech.get("enough_data"):
        fillers = ", ".join(f["term"] for f in speech.get("fillers", [])[:5])
        parts.append(
            f"They write ~{speech['avg_sentence_words']} words a sentence"
            + (f"; they lean on: {fillers}" if fillers else "")
        )
    return "\n".join(parts)


async def answer_as_you(
    session: AsyncSession, user_id: uuid.UUID, question: str, *, router: Any = None
) -> dict[str, Any]:
    q = (question or "").strip()
    if len(q) < 3:
        return {"answer": None, "grounded": False}
    context = await _context(session, user_id)
    if not context.strip():
        return {"answer": None, "grounded": False,
                "reason": "Not enough of you learned yet — write your profile and chat a while."}

    try:
        from jarvis.llm.router import LLMRouter
        from jarvis.llm.types import Message

        r = router or LLMRouter(session)
        resp = await r.chat(
            [Message("system", _SYSTEM),
             Message("user", f"Who I am:\n{context}\n\nAnswer as me: {q}")],
            user_id=user_id,
            max_tokens=400,
        )
        answer = (resp.text or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — no model reachable
        log.info("twin_unavailable", error=str(exc)[:160])
        return {"answer": None, "grounded": True,
                "reason": "The local model isn't reachable right now."}
    return {"answer": answer, "grounded": True}
