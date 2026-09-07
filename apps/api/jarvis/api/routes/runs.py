"""One agent run, step by step — what the chat shows under a reply.

The steps are the ``actions`` the run proposed, each with its policy outcome and the
verifier's verdict. This is the "show your work" panel: not the model's prose about
what it did, but the rows it actually produced.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.errors import NotFound
from jarvis.db.models.agent import Action, AgentRun, Evidence

router = APIRouter(prefix="/v1/runs", tags=["runs"])


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    run = await session.get(AgentRun, run_id)
    if run is None or run.user_id != user.id:
        raise NotFound("Run")
    actions = (
        await session.scalars(
            select(Action).where(Action.run_id == run.id).order_by(Action.created_at)
        )
    ).all()
    verdicts: dict[str, str] = {}
    if actions:
        for ev in (
            await session.scalars(
                select(Evidence).where(Evidence.action_id.in_([a.id for a in actions]))
            )
        ).all():
            current = verdicts.get(str(ev.action_id))
            verdicts[str(ev.action_id)] = (
                "failed"
                if "failed" in (current, ev.verdict)
                else ev.verdict
                if current in (None, "inconclusive")
                else current
            )
    return {
        "id": str(run.id),
        "status": run.status,
        "state": run.state,
        "stop_reason": run.stop_reason,
        "trigger": run.trigger,
        "steps": [
            {
                "id": str(a.id),
                "tool": a.tool,
                "risk": a.risk,
                "status": a.status,
                "verdict": verdicts.get(str(a.id)),
                "rationale": a.rationale,
                "args": {k: v for k, v in (a.args or {}).items() if k != "scope_bookmark"},
                "simulated": a.simulate,
            }
            for a in actions
        ],
    }
