"""The timeline: everything the system did, in one list, newest first.

"Track everything in the app" means one screen, not seven. Actions (with their verdict
and any artifact), agent runs, notifications, approvals, heartbeat alerts and connector
syncs are all here — each row keeps its correlation id, so a tap can follow one request
across every hop it made.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.db.models.agent import Action, AgentRun, Evidence
from jarvis.db.models.ops import Artifact, AuditLog

router = APIRouter(prefix="/v1", tags=["timeline"])

# Audit rows that are worth a line of their own; the rest are already represented by
# the action or run they describe.
_NOTEWORTHY = {
    "notification.sent",
    "approval.decided",
    "heartbeat.alerted",
    "action.dispatch_refused",
    "action.expired_while_offline",
    "agent.paused",
    "agent.resumed",
    "device.paired",
    "device.revoked",
    "kill_switch",
    "vision.described",
    "routine.finished",
    "triage.classified",
}


@router.get("/timeline")
async def timeline(
    user: CurrentUser, session: SessionDep, limit: int = 100
) -> list[dict[str, Any]]:
    limit = min(limit, 300)
    actions = (
        await session.scalars(
            select(Action).where(Action.user_id == user.id).order_by(Action.id.desc()).limit(limit)
        )
    ).all()
    runs = (
        await session.scalars(
            select(AgentRun)
            .where(AgentRun.user_id == user.id)
            .order_by(AgentRun.id.desc())
            .limit(limit)
        )
    ).all()
    audits = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.user_id == user.id, AuditLog.action.in_(_NOTEWORTHY))
            .order_by(AuditLog.id.desc())
            .limit(limit)
        )
    ).all()

    action_ids = [a.id for a in actions]
    verdicts: dict[str, str] = {}
    artifacts: dict[str, list[dict[str, Any]]] = {}
    if action_ids:
        for ev in (
            await session.scalars(select(Evidence).where(Evidence.action_id.in_(action_ids)))
        ).all():
            # The worst check wins: one failed requirement fails the action.
            current = verdicts.get(str(ev.action_id))
            verdicts[str(ev.action_id)] = (
                "failed"
                if "failed" in (current, ev.verdict)
                else ev.verdict
                if current in (None, "inconclusive")
                else current
            )
        for art in (
            await session.scalars(select(Artifact).where(Artifact.action_id.in_(action_ids)))
        ).all():
            artifacts.setdefault(str(art.action_id), []).append(
                {
                    "id": str(art.id),
                    "kind": art.kind,
                    "content_type": art.content_type,
                    "url": f"/v1/artifacts/{art.id}",
                }
            )

    entries: list[dict[str, Any]] = []
    for a in actions:
        entries.append(
            {
                "at": a.created_at.isoformat(),
                "kind": "action",
                "title": a.tool,
                "status": a.status,
                "risk": a.risk,
                "verdict": verdicts.get(str(a.id)),
                "detail": {k: v for k, v in (a.args or {}).items() if k != "scope_bookmark"},
                "rationale": a.rationale,
                "correlation_id": a.correlation_id,
                "id": str(a.id),
                "artifacts": artifacts.get(str(a.id), []),
                "simulated": a.simulate,
            }
        )
    for r in runs:
        entries.append(
            {
                "at": r.created_at.isoformat(),
                "kind": "run",
                "title": f"{r.trigger} run",
                "status": r.status,
                "detail": {"stop_reason": r.stop_reason, "steps": r.step_index},
                "correlation_id": r.correlation_id,
                "id": str(r.id),
            }
        )
    for e in audits:
        entries.append(
            {
                "at": e.created_at.isoformat(),
                "kind": "event",
                "title": e.action,
                "status": None,
                "detail": e.detail or {},
                "actor": e.actor,
                "correlation_id": e.correlation_id,
                "id": str(e.id),
            }
        )

    entries.sort(key=lambda x: x["at"], reverse=True)
    return entries[:limit]
