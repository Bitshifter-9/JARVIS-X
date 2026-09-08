"""Communication coach (#37): a single read on how you're keeping up with people — who's
waiting on you, who you've gone quiet on, and how your writing lands — each with a concrete
fix. Composes the comm-specific signals already computed (owed replies #28, relationship
cadence #16, speech pattern #12); deterministic, no model.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def comm_coach(
    session: AsyncSession, user_id: uuid.UUID, *, tz: str = "UTC"
) -> dict[str, Any]:
    """Concrete communication observations, most actionable first."""
    from jarvis.services.proactivity import owed_replies
    from jarvis.services.relationships import relationships
    from jarvis.services.speech import speech_profile
    from jarvis.services.triage import sender_name

    obs: list[dict[str, Any]] = []

    owed = await owed_replies(session, user_id, limit=6)
    if owed:
        names = ", ".join(sender_name(o["sender"]) for o in owed[:3])
        obs.append({
            "kind": "waiting",
            "text": f"{len(owed)} waiting on your reply: {names}"
                    + ("…" if len(owed) > 3 else "") + ".",
            "fix": "Clear the oldest first — a one-line reply counts.",
        })

    quiet = [r for r in await relationships(session, user_id, limit=15) if r["quiet"]]
    if quiet:
        q = quiet[0]
        obs.append({
            "kind": "quiet",
            "text": f"You've gone quiet on {q['name']} — usually every "
                    f"{q['cadence_days']}d, now {q['days_since']}d.",
            "fix": "Send a quick hello before it drifts further.",
        })

    speech = await speech_profile(session, user_id)
    if speech.get("enough_data"):
        avg = speech["avg_sentence_words"]
        obs.append({
            "kind": "tone",
            "text": f"You write ~{avg} words a sentence.",
            "fix": ("Shorter lines read warmer and get replies faster."
                    if avg > 20 else "Your concise style reads clearly — keep it."),
        })

    return {"observations": obs}
