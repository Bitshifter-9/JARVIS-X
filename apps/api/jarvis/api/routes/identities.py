"""Linking a channel identity to your account.

A Slack user id, a Telegram chat id, an OpenClaw handle — none of these is an account.
Each is an ``identities`` row that points at one (blueprint §2), and until that row
exists a message from that id is a stranger's and is dropped. This is where the row is
made, by the signed-in owner, never by the channel itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.errors import Conflict, NotFound
from jarvis.db.models.identity import Identity
from jarvis.db.models.ops import AuditLog

router = APIRouter(prefix="/v1/identities", tags=["identities"])

LINKABLE = {"slack", "telegram", "openclaw", "whatsapp"}


class LinkRequest(BaseModel):
    provider: str = Field(max_length=32)
    subject: str = Field(min_length=1, max_length=255, description="The provider's id for you")


def _out(identity: Identity) -> dict[str, Any]:
    return {
        "id": str(identity.id),
        "provider": identity.provider,
        "subject": identity.subject,
        "is_owner": identity.is_owner,
        "linked_at": identity.created_at.isoformat(),
        "revoked_at": identity.revoked_at.isoformat() if identity.revoked_at else None,
    }


@router.get("")
async def list_identities(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(Identity)
            .where(Identity.user_id == user.id, Identity.provider.in_(LINKABLE))
            .order_by(Identity.created_at)
        )
    ).all()
    return [_out(i) for i in rows]


@router.post("", status_code=201)
async def link(body: LinkRequest, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Claim a channel id as yours. One id belongs to one account, ever."""
    provider = body.provider.lower()
    if provider not in LINKABLE:
        raise Conflict(f"{provider} is not a linkable channel")
    existing = await session.scalar(
        select(Identity).where(Identity.provider == provider, Identity.subject == body.subject)
    )
    if existing is not None:
        if existing.user_id != user.id:
            # Not "already taken" — that would confirm the id exists on another account.
            raise Conflict("That id cannot be linked")
        if existing.revoked_at is not None:
            existing.revoked_at = None
            await session.flush()
        return _out(existing)

    identity = Identity(user_id=user.id, provider=provider, subject=body.subject, is_owner=True)
    session.add(identity)
    session.add(
        AuditLog(
            user_id=user.id,
            actor="user",
            action="identity.linked",
            subject_type="identity",
            subject_id=body.subject,
            detail={"provider": provider},
        )
    )
    await session.flush()
    return _out(identity)


@router.delete("/{identity_id}")
async def unlink(identity_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    identity = await session.get(Identity, identity_id)
    if identity is None or identity.user_id != user.id:
        raise NotFound("Identity")
    identity.revoked_at = datetime.now(UTC)
    session.add(
        AuditLog(
            user_id=user.id,
            actor="user",
            action="identity.revoked",
            subject_type="identity",
            subject_id=identity.subject,
            detail={"provider": identity.provider},
        )
    )
    await session.flush()
    return _out(identity)
