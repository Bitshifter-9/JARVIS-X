"""Standing permissions (PLAN.md 10.9.2): an envelope inside which an R2 action runs
without a fresh approval — one tool, optional argument constraints, a per-day rate,
an expiry, and one tap to revoke."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.errors import Conflict, NotFound
from jarvis.db.models.ops import AuditLog, StandingPermission
from jarvis.services.policy.rules import Risk, rule_for

router = APIRouter(prefix="/v1/permissions", tags=["permissions"])


class PermissionIn(BaseModel):
    tool: str = Field(max_length=64)
    conditions: dict[str, Any] = Field(default_factory=dict)
    max_per_day: int | None = Field(default=None, ge=1, le=100)
    days: int = Field(default=7, ge=1, le=90)


def _out(p: StandingPermission) -> dict[str, Any]:
    conditions = dict(p.conditions or {})
    return {
        "id": str(p.id),
        "tool": p.tool,
        "conditions": {k: v for k, v in conditions.items() if not k.startswith("_")},
        "max_per_day": conditions.get("_max_per_day"),
        "max_risk": p.max_risk,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        "created_at": p.created_at.isoformat(),
    }


# The R2 device verbs a tap would otherwise have to approve one by one (PLAN.md 11.8).
TRUSTED_DEVICE_TOOLS = (
    "mac.capture_screen",
    "mac.describe_screen",
    "mac.type_text",
    "mac.press_key",
    "mac.send_file",
    "mac.whatsapp_send",
    "phone.camera",
    "phone.whatsapp_send",
    "phone.locate",
)


class TrustIn(BaseModel):
    days: int = Field(default=30, ge=1, le=90)


@router.post("/trust-devices", status_code=201)
async def trust_devices(body: TrustIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """"My own Mac and phone may act on my behalf without asking": one envelope per
    device verb, revocable in one tap, expiring on its own."""
    await _revoke_trust(session, user.id)
    expires = datetime.now(UTC) + timedelta(days=body.days)
    rows = [
        StandingPermission(
            user_id=user.id, tool=tool, conditions={"_trust": "devices"},
            max_risk=rule_for(tool).risk.value, expires_at=expires,
        )
        for tool in TRUSTED_DEVICE_TOOLS
    ]
    session.add_all(rows)
    session.add(
        AuditLog(
            user_id=user.id, actor="user", action="permission.granted",
            subject_type="standing_permission", subject_id="trust-devices",
            detail={"tools": list(TRUSTED_DEVICE_TOOLS), "days": body.days},
        )
    )
    await session.flush()
    return {"granted": len(rows), "expires_at": expires.isoformat()}


@router.get("/trust-devices")
async def trust_status(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    rows = (
        await session.scalars(
            select(StandingPermission).where(
                StandingPermission.user_id == user.id,
                StandingPermission.revoked_at.is_(None),
                StandingPermission.conditions["_trust"].astext == "devices",
            )
        )
    ).all()
    live = [r for r in rows if r.expires_at is None or r.expires_at > now]
    return {
        "trusted": len(live) >= len(TRUSTED_DEVICE_TOOLS),
        "expires_at": min((r.expires_at for r in live if r.expires_at), default=None),
    }


@router.delete("/trust-devices", status_code=204)
async def untrust_devices(user: CurrentUser, session: SessionDep) -> None:
    await _revoke_trust(session, user.id)


async def _revoke_trust(session, user_id) -> int:  # noqa: ANN001
    rows = (
        await session.scalars(
            select(StandingPermission).where(
                StandingPermission.user_id == user_id,
                StandingPermission.revoked_at.is_(None),
                StandingPermission.conditions["_trust"].astext == "devices",
            )
        )
    ).all()
    for row in rows:
        row.revoked_at = datetime.now(UTC)
    await session.flush()
    return len(rows)


@router.get("")
async def list_permissions(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(StandingPermission)
            .where(
                StandingPermission.user_id == user.id,
                StandingPermission.revoked_at.is_(None),
            )
            .order_by(StandingPermission.created_at.desc())
        )
    ).all()
    now = datetime.now(UTC)
    return [_out(p) for p in rows if p.expires_at is None or p.expires_at > now]


@router.post("", status_code=201)
async def grant(body: PermissionIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    rule = rule_for(body.tool)
    if not rule.standing_permission_allowed or rule.risk in (Risk.R3, Risk.R4):
        raise Conflict(f"{body.tool} cannot be pre-approved; it always asks")
    conditions = {k: v for k, v in body.conditions.items() if not k.startswith("_")}
    if body.max_per_day:
        conditions["_max_per_day"] = body.max_per_day
    row = StandingPermission(
        user_id=user.id,
        tool=body.tool,
        conditions=conditions,
        max_risk=rule.risk.value,
        expires_at=datetime.now(UTC) + timedelta(days=body.days),
    )
    session.add(row)
    await session.flush()
    session.add(
        AuditLog(
            user_id=user.id,
            actor="user",
            action="permission.granted",
            subject_type="standing_permission",
            subject_id=str(row.id),
            detail={"tool": body.tool, "conditions": conditions, "days": body.days},
        )
    )
    await session.flush()
    await session.refresh(row)
    return _out(row)


@router.delete("/{permission_id}", status_code=204)
async def revoke(permission_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> None:
    row = await session.scalar(
        select(StandingPermission).where(
            StandingPermission.id == permission_id, StandingPermission.user_id == user.id
        )
    )
    if row is None or row.revoked_at is not None:
        raise NotFound("Permission")
    row.revoked_at = datetime.now(UTC)
    session.add(
        AuditLog(
            user_id=user.id,
            actor="user",
            action="permission.revoked",
            subject_type="standing_permission",
            subject_id=str(row.id),
            detail={"tool": row.tool},
        )
    )
    await session.flush()
