"""Quick-capture: one fleeting thought, filed where it belongs (second-brain #9).

A dated thought becomes a deadline; anything else becomes an episodic memory — so it is
recalled and shows up in life search. Both paths are free: a regex and a local embedding,
no cloud call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings

router = APIRouter(prefix="/v1/capture", tags=["capture"])


class CaptureIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("")
async def capture(body: CaptureIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    from jarvis.services.extraction.regex_fallback import extract_deadline
    from jarvis.services.extraction.resolver import resolve
    from jarvis.services.goal import GoalService

    text = body.text.strip()
    now = datetime.now(UTC)
    tz = user.timezone or get_settings().timezone

    guessed = extract_deadline(text, text, now, require_cue=False)
    if guessed is not None and guessed.has_deadline:
        due = None
        title = (guessed.title or text)[:500]
        try:
            resolved = resolve(guessed, received_at=now, default_timezone=tz)
            due = resolved.due_at if resolved else None
        except Exception:  # noqa: BLE001 — a bad parse just means no date
            due = None
        if due is not None:
            task = await GoalService(session).create_task(
                user.id, title=title, due_at=due, timezone=tz,
                evidence_span=text[:500], confidence=0.55,
            )
            await session.commit()
            return {"kind": "task", "id": str(task.id), "title": task.title,
                    "due": task.due_at.isoformat() if task.due_at else None}

    # Not dated → keep it as a memory so it is recalled and searchable.
    from jarvis.services.memory import MemoryService

    memory = await MemoryService(session).remember(
        user.id, content=text, kind="episodic", importance=0.5,
        provenance={"source": "capture", "at": now.isoformat()},
    )
    await session.commit()
    if memory is None:  # refused (looked like a secret)
        return {"kind": "refused"}
    return {"kind": "note", "id": str(memory.id), "title": text[:80]}
