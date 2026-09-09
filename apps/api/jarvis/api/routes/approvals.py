"""Approvals, simulation and the kill switch."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings
from jarvis.db.models.agent import Approval
from jarvis.db.models.ops import AuditLog
from jarvis.db.queue import JobQueue
from jarvis.services.identity import IdentityService
from jarvis.services.tool_gateway import ToolGateway

router = APIRouter(prefix="/v1", tags=["approvals"])


class SimulateRequest(BaseModel):
    tool: str = Field(max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    device_id: uuid.UUID | None = None


class DecisionRequest(BaseModel):
    approved: bool
    decided_by: str = Field(default="app", max_length=64)


class ApprovalOut(BaseModel):
    id: str
    action_id: str
    decision: str | None
    expires_at: str
    requires_local_confirmation: bool
    locally_confirmed: bool
    # What is being approved — a decision needs more than an id.
    tool: str | None = None
    summary: str | None = None


def _summarize(tool: str, args: dict) -> str:
    """One human line: the thing that will happen if this is approved."""
    if tool == "youtube.upload":
        return f"Upload to YouTube: {args.get('title', 'video')}"
    if tool == "youtube.reply":
        return f"Reply to a comment: {str(args.get('text', ''))[:120]}"
    body = args.get("title") or args.get("body") or args.get("text") or ""
    return f"{tool}: {str(body)[:120]}".rstrip(": ")


class ActionRequest(BaseModel):
    tool: str = Field(max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    device_id: uuid.UUID | None = None
    simulate: bool = False


@router.post("/actions", status_code=202)
async def run_action(body: ActionRequest, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """One typed action, straight from the app — the Mac panel's buttons land here.

    Same gate as the agent: proposed through the tool gateway, so an R1 runs now, an R2
    waits for the approval this returns, and an R4 is refused. A device action is
    addressed to the paired device and verified when the device reports back.
    """
    from jarvis.services.agent.executor import dispatch_action
    from jarvis.services.policy import Decision

    device_id = body.device_id
    family = body.tool.split(".", 1)[0]
    if device_id is None and family in ("mac", "phone"):
        platform = "macos" if family == "mac" else "android"
        device_id = await _default_device(session, user.id, platform)

    proposal = await ToolGateway(session).propose(
        user.id,
        tool=body.tool,
        args=body.args,
        device_id=device_id,
        simulate=body.simulate,
        rationale="requested from the app",
    )
    if proposal.policy.decision is Decision.DENY:
        return {
            "status": "denied",
            "reason": proposal.policy.reason,
            "action_id": str(proposal.action.id),
        }
    if proposal.needs_approval:
        # The owner tapped this button in their own signed-in app, on their own paired
        # device: that tap *is* the approval for an R2 device verb. R3 still needs the
        # Mac's local confirmation, and anything that is not a device verb still waits.
        if family in ("mac", "phone") and not proposal.approval.requires_local_confirmation:
            await ToolGateway(session).decide(
                user.id, proposal.approval.id, approved=True, decided_by="app-tap"
            )
            outcome = await dispatch_action(session, proposal.action.id, simulate=body.simulate)
            return {**outcome, "approved_by": "app-tap"}
        return {
            "status": "awaiting_approval",
            "approval_id": str(proposal.approval.id),
            "action_id": str(proposal.action.id),
            "reason": proposal.policy.reason,
        }
    return await dispatch_action(session, proposal.action.id, simulate=body.simulate)


async def _default_device(session, user_id: uuid.UUID, platform: str) -> uuid.UUID | None:  # noqa: ANN001
    from jarvis.services.device import DeviceService

    devices = DeviceService(session)
    candidates = [
        d for d in await devices.list_devices(user_id) if d.is_active and d.platform == platform
    ]
    for d in candidates:
        if await devices.has_live_connection(d.id):
            return d.id
    return candidates[0].id if candidates else None


@router.post("/actions/simulate")
async def simulate(body: SimulateRequest, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Show exactly what an action would do, without doing it.

    The returned ``payload_hash`` is the hash a real execution binds to, which makes
    "flip SIMULATE to EXECUTE on the same plan" a checkable claim.
    """
    preview = await ToolGateway(session).simulate(
        user.id, tool=body.tool, args=body.args, device_id=body.device_id
    )
    return {
        "tool": preview.tool,
        "risk": preview.risk,
        "args": preview.args,
        "recipients": preview.recipients,
        "expected_evidence": preview.expected_evidence,
        "command_preview": preview.command_preview,
        "estimated_seconds": preview.estimated_seconds,
        "payload_hash": preview.payload_hash,
        "policy": {"decision": preview.policy_decision, "reason": preview.policy_reason},
    }


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_pending(user: CurrentUser, session: SessionDep) -> list[ApprovalOut]:
    from jarvis.db.models.agent import Action

    rows = (
        await session.execute(
            select(Approval, Action)
            .join(Action, Action.id == Approval.action_id)
            .where(
                Approval.user_id == user.id,
                Approval.decision.is_(None),
                Approval.expires_at > func.now(),
            )
            .order_by(Approval.created_at)
        )
    ).all()
    return [
        ApprovalOut(
            id=str(a.id),
            action_id=str(a.action_id),
            decision=a.decision,
            expires_at=a.expires_at.isoformat(),
            requires_local_confirmation=a.requires_local_confirmation,
            locally_confirmed=a.local_confirmed_at is not None,
            tool=action.tool,
            summary=_summarize(action.tool, action.args or {}),
        )
        for a, action in rows
    ]


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalOut)
async def decide(
    approval_id: uuid.UUID, body: DecisionRequest, user: CurrentUser, session: SessionDep
) -> ApprovalOut:
    approval = await ToolGateway(session).decide(
        user.id, approval_id, approved=body.approved, decided_by=body.decided_by
    )
    return ApprovalOut(
        id=str(approval.id),
        action_id=str(approval.action_id),
        decision=approval.decision,
        expires_at=approval.expires_at.isoformat(),
        requires_local_confirmation=approval.requires_local_confirmation,
        locally_confirmed=approval.local_confirmed_at is not None,
    )


@router.post("/agent/pause")
async def pause(user: CurrentUser, session: SessionDep, reason: str = "user requested") -> dict:
    """The kill switch.

    Cancels queued work and revokes sessions. It never deletes evidence — it records who
    invoked it and why (blueprint §9).
    """
    settings = get_settings()
    settings.global_pause = True

    cancelled = await JobQueue(session).cancel_pending(
        user_id=user.id, reason=f"kill switch: {reason}"
    )
    revoked = await IdentityService(session).revoke_all_sessions(user.id)
    session.add(
        AuditLog(
            user_id=user.id,
            actor="user",
            action="agent.paused",
            subject_type="user",
            subject_id=str(user.id),
            detail={"reason": reason, "jobs_cancelled": cancelled, "sessions_revoked": revoked},
        )
    )
    return {
        "paused": True,
        "jobs_cancelled": cancelled,
        "sessions_revoked": revoked,
        "evidence_preserved": True,
    }


@router.post("/agent/resume")
async def resume(user: CurrentUser, session: SessionDep) -> dict:
    get_settings().global_pause = False
    session.add(
        AuditLog(
            user_id=user.id,
            actor="user",
            action="agent.resumed",
            subject_type="user",
            subject_id=str(user.id),
            detail={},
        )
    )
    return {"paused": False}
