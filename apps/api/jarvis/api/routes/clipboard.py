"""A clipboard synced across your devices (FEATURES-50 #26).

Push text from one device, pull it on another. Stored on the profile — it never syncs
device-to-device, it goes through the one account like everything else.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.profile import get_profile

router = APIRouter(prefix="/v1/clipboard", tags=["clipboard"])

MAX_CHARS = 20_000


class ClipIn(BaseModel):
    text: str = Field(max_length=MAX_CHARS)
    device: str | None = Field(default=None, max_length=64)


@router.get("")
async def get_clipboard(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    profile = await get_profile(session, user.id)
    return profile.clipboard or {}


MAX_HISTORY = 50


@router.post("")
async def set_clipboard(body: ClipIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    profile = await get_profile(session, user.id)
    now = datetime.now(UTC).isoformat()
    prev = profile.clipboard or {}
    history = list(prev.get("history") or [])
    # Prepend the new clip, skip an immediate duplicate, cap the list (searchable history #7).
    if body.text.strip() and (not history or history[0].get("text") != body.text):
        history.insert(0, {"text": body.text, "device": body.device, "at": now})
        history = history[:MAX_HISTORY]
    profile.clipboard = {
        "text": body.text,
        "device": body.device,
        "updated_at": now,
        "history": history,
    }
    await session.flush()
    return {"text": body.text, "device": body.device, "updated_at": now}


@router.get("/history")
async def clipboard_history(
    user: CurrentUser, session: SessionDep, q: str | None = None
) -> list[dict[str, Any]]:
    """Everything you've copied, newest first — searchable (second-brain #7)."""
    profile = await get_profile(session, user.id)
    history = list((profile.clipboard or {}).get("history") or [])
    if q:
        needle = q.lower()
        history = [h for h in history if needle in str(h.get("text", "")).lower()]
    return history
