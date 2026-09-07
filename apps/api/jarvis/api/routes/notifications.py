"""Where alerts go: the phone's push token, a Telegram chat, a WhatsApp number, a phone.

One row per (channel, address). The escalation ladder walks the enabled rows in rank
order — push first, the call last — so registering a push token here is what makes the
phone the first thing that buzzes.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.errors import Conflict, NotFound
from jarvis.db.models.ops import NotificationEndpoint
from jarvis.services.notification.policy import ESCALATION_ORDER, Channel

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


class EndpointIn(BaseModel):
    channel: str = Field(max_length=24)
    address: str = Field(min_length=1, max_length=512)
    enabled: bool = True
    quiet_hours: str | None = Field(default=None, max_length=32)


def _out(e: NotificationEndpoint) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "channel": e.channel,
        # A push token is long and meaningless; a number or chat id is worth seeing.
        "address": e.address if e.channel != Channel.PUSH.value else f"…{e.address[-8:]}",
        "enabled": e.enabled,
        "escalation_rank": e.escalation_rank,
        "quiet_hours": e.quiet_hours,
        "last_used_at": e.last_used_at.isoformat() if e.last_used_at else None,
    }


@router.get("/endpoints")
async def list_endpoints(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(NotificationEndpoint)
            .where(NotificationEndpoint.user_id == user.id)
            .order_by(NotificationEndpoint.escalation_rank, NotificationEndpoint.created_at)
        )
    ).all()
    return [_out(e) for e in rows]


@router.put("/endpoints")
async def upsert_endpoint(
    body: EndpointIn, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """Register or update one destination. Idempotent on (channel, address), so the phone
    can re-send its token on every launch."""
    try:
        channel = Channel(body.channel)
    except ValueError as exc:
        raise Conflict(f"unknown channel {body.channel}") from exc

    existing = await session.scalar(
        select(NotificationEndpoint).where(
            NotificationEndpoint.user_id == user.id,
            NotificationEndpoint.channel == channel.value,
            NotificationEndpoint.address == body.address,
        )
    )
    if existing is None:
        existing = NotificationEndpoint(
            user_id=user.id,
            channel=channel.value,
            address=body.address,
            escalation_rank=ESCALATION_ORDER.index(channel),
        )
        session.add(existing)
    existing.enabled = body.enabled
    existing.quiet_hours = body.quiet_hours
    await session.flush()
    return _out(existing)


@router.delete("/endpoints/{endpoint_id}")
async def delete_endpoint(
    endpoint_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    row = await session.get(NotificationEndpoint, endpoint_id)
    if row is None or row.user_id != user.id:
        raise NotFound("Notification endpoint")
    await session.delete(row)
    await session.flush()
    return {"deleted": True}
