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


@router.post("")
async def set_clipboard(body: ClipIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    profile = await get_profile(session, user.id)
    profile.clipboard = {
        "text": body.text,
        "device": body.device,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await session.flush()
    return profile.clipboard
