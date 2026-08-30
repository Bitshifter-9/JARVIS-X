"""The tool gateway: propose, approve, simulate, dispatch, verify.

Where the safety guarantees become operations on rows:

* A **proposal** is written with the evidence it expects. No expected evidence, no dispatch.
* An **approval** stores a hash over the whole payload, so an edit is a new proposal.
* **Simulation** runs planning, policy and verification preconditions with effectful tools
  replaced by simulators — and executes the *same hashed plan* on demand (blueprint §9).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.core.correlation import get_correlation_id
from jarvis.core.errors import Conflict, Forbidden, NotFound, PolicyDenied
from jarvis.core.ids import uuid7
from jarvis.core.logging import get_logger
from jarvis.core.security import approval_payload_hash
from jarvis.db.models.agent import Action, ActionStatus, Approval, Risk
from jarvis.db.models.ops import AuditLog
from jarvis.services.policy import (
    Decision,
    PolicyResult,
    PolicyService,
    bind_expected,
    manifest_for,
)
from jarvis.services.tool_gateway.templates import COMMAND_TEMPLATES
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

DEFAULT_ACTION_TTL = timedelta(minutes=15)
DEFAULT_APPROVAL_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class Proposal:
    action: Action
    policy: PolicyResult
    approval: Approval | None

    @property
    def needs_approval(self) -> bool:
        return self.approval is not None


@dataclass(frozen=True)
class SimulationPreview:
    """What an action *would* do. Shown before anything happens (blueprint §9)."""

    tool: str
    risk: str
    args: dict[str, Any]
    expected_evidence: list[str]
    recipients: list[str]
    command_preview: str | None
    estimated_seconds: int
    payload_hash: str
    policy_decision: str
    policy_reason: str


class ToolGateway:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.policy = PolicyService(session)

    async def propose(
        self,
        user_id: uuid.UUID,
        *,
        tool: str,
        args: dict[str, Any],
        run_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        from_untrusted_source: bool = False,
        simulate: bool = False,
        rationale: str | None = None,
        ttl: timedelta = DEFAULT_ACTION_TTL,
    ) -> Proposal:
        """Evaluate a proposed action and persist it with whatever it now requires."""
        context = await self.policy.load_context(
            user_id, tool, args, device_id=device_id, from_untrusted_source=from_untrusted_source
        )
        decision = await self.policy.evaluate(context)

        manifest = manifest_for(tool)
        expected = bind_expected(manifest.verify if manifest else (), args)
        expires_at = datetime.now(UTC) + ttl

        # Generated here, not left to the column default: the approval must reference
        # this action's id, and a column default is only applied at flush time.
        action_id = uuid7()

        action = Action(
            id=action_id,
            run_id=run_id,
            user_id=user_id,
            tool=tool,
            args=args,
            risk=decision.risk.value,
            expected=expected,
            idempotency_key=f"{user_id}:{tool}:{uuid.uuid4()}",
            timeout_seconds=manifest.timeout_seconds if manifest else 30,
            expires_at=expires_at,
            simulate=simulate,
            device_id=device_id,
            policy_version=decision.policy_version,
            rationale=rationale,
            correlation_id=get_correlation_id(),
            status=ActionStatus.PROPOSED.value,
        )

        approval: Approval | None = None
        if decision.decision is Decision.DENY:
            action.status = ActionStatus.DENIED.value
        elif decision.decision is Decision.REQUIRE_APPROVAL:
            action.status = ActionStatus.AWAITING_APPROVAL.value
            approval = Approval(
                action_id=action_id,
                user_id=user_id,
                payload_hash=approval_payload_hash(
                    tool=tool,
                    args=args,
                    user_id=str(user_id),
                    device_id=str(device_id) if device_id else None,
                    expires_at=expires_at,
                ),
                expires_at=datetime.now(UTC) + DEFAULT_APPROVAL_TTL,
                requires_local_confirmation=decision.requires_local_confirmation,
            )
        else:
            action.status = ActionStatus.APPROVED.value

        self.session.add(action)
        if approval is not None:
            self.session.add(approval)
        await self.session.flush()

        await self._audit(
            user_id, "action.proposed", "action", str(action.id),
            {
                "tool": tool,
                "risk": decision.risk.value,
                "decision": decision.decision.value,
                "reason": decision.reason,
                "simulate": simulate,
            },
        )
        log.info(
            "action_proposed",
            action_id=str(action.id), tool=tool,
            risk=decision.risk.value, decision=decision.decision.value,
        )
        return Proposal(action=action, policy=decision, approval=approval)

    # ── simulation ─────────────────────────────────────────────────────
    async def simulate(
        self,
        user_id: uuid.UUID,
        *,
        tool: str,
        args: dict[str, Any],
        device_id: uuid.UUID | None = None,
    ) -> SimulationPreview:
        """Show the exact plan without performing it.

        The hash returned here is the hash the real execution will be bound to, which is
        what makes "flip SIMULATE to EXECUTE on the same plan" a checkable claim rather
        than a demo flourish.
        """
        context = await self.policy.load_context(user_id, tool, args, device_id=device_id)
        decision = await self.policy.evaluate(context)
        manifest = manifest_for(tool)
        expires_at = datetime.now(UTC) + DEFAULT_ACTION_TTL

        command_preview = None
        if tool == "mac.run_template":
            template = COMMAND_TEMPLATES.get(args.get("template", ""))
            if template is not None:
                command_preview = template.preview(args.get("params", {}))

        return SimulationPreview(
            tool=tool,
            risk=decision.risk.value,
            args=args,
            expected_evidence=list(manifest.verify) if manifest else [],
            recipients=_recipients(args),
            command_preview=command_preview,
            estimated_seconds=manifest.timeout_seconds if manifest else 30,
            payload_hash=approval_payload_hash(
                tool=tool, args=args, user_id=str(user_id),
                device_id=str(device_id) if device_id else None, expires_at=expires_at,
            ),
            policy_decision=decision.decision.value,
            policy_reason=decision.reason,
        )

    # ── approval ───────────────────────────────────────────────────────
    async def decide(
        self,
        user_id: uuid.UUID,
        approval_id: uuid.UUID,
        *,
        approved: bool,
        decided_by: str,
    ) -> Approval:
        approval = await self.session.scalar(
            select(Approval).where(Approval.id == approval_id, Approval.user_id == user_id)
        )
        if approval is None:
            raise NotFound("Approval")
        if approval.decision is not None:
            raise Conflict("This approval has already been decided")
        if approval.expires_at <= datetime.now(UTC):
            raise Conflict("This approval has expired; a new proposal is needed")

        approval.decision = "approved" if approved else "rejected"
        approval.decided_by = decided_by
        approval.decided_at = datetime.now(UTC)

        action = await self.session.get(Action, approval.action_id)
        if action is not None:
            action.status = (
                ActionStatus.APPROVED.value if approved else ActionStatus.REJECTED.value
            )
        await self.session.flush()

        await self._audit(
            user_id, "approval.decided", "approval", str(approval.id),
            {"decision": approval.decision, "decided_by": decided_by,
             "tool": action.tool if action else None},
        )
        log.info(
            "approval_decided",
            approval_id=str(approval.id), decision=approval.decision, by=decided_by,
        )
        return approval

    async def confirm_locally(self, user_id: uuid.UUID, approval_id: uuid.UUID) -> Approval:
        """The second factor for R3, supplied by the Mac itself."""
        approval = await self.session.scalar(
            select(Approval).where(Approval.id == approval_id, Approval.user_id == user_id)
        )
        if approval is None:
            raise NotFound("Approval")
        if not approval.requires_local_confirmation:
            raise Conflict("This action does not require a local confirmation")
        approval.local_confirmed_at = datetime.now(UTC)
        await self.session.flush()
        return approval

    # ── dispatch ───────────────────────────────────────────────────────
    async def authorize_dispatch(self, action_id: uuid.UUID) -> Action:
        """The executor's own check, run immediately before the tool is invoked.

        Raises rather than returning a verdict, because a caller that ignores a returned
        verdict is a bug that dispatches an unapproved action.
        """
        # populate_existing: re-read the row rather than trusting the identity map.
        # The executor may have loaded this action earlier in the same transaction, and
        # revalidation is worthless if it inspects a stale copy.
        action = await self.session.get(Action, action_id, populate_existing=True)
        if action is None:
            raise NotFound("Action")

        result = await self.policy.revalidate_for_dispatch(action)
        if result.decision is not Decision.ALLOW:
            action.status = (
                ActionStatus.EXPIRED.value
                if "expired" in result.reason.lower()
                else ActionStatus.DENIED.value
            )
            await self.session.flush()
            await self._audit(
                action.user_id, "action.dispatch_refused", "action", str(action.id),
                {"tool": action.tool, "reason": result.reason},
            )
            log.warning(
                "dispatch_refused",
                action_id=str(action.id), tool=action.tool, reason=result.reason,
            )
            raise PolicyDenied(result.reason, risk=action.risk)

        action.status = ActionStatus.DISPATCHED.value
        await self.session.flush()
        await self._audit(
            action.user_id, "action.dispatched", "action", str(action.id),
            {"tool": action.tool, "risk": action.risk},
        )
        return action

    async def _audit(
        self,
        user_id: uuid.UUID | None,
        action: str,
        subject_type: str,
        subject_id: str,
        detail: dict[str, Any],
    ) -> None:
        self.session.add(
            AuditLog(
                user_id=user_id,
                actor="agent",
                action=action,
                subject_type=subject_type,
                subject_id=subject_id,
                detail=detail,
                correlation_id=get_correlation_id(),
            )
        )


def _recipients(args: dict[str, Any]) -> list[str]:
    """Who this action would reach. Surfaced prominently, because "send a message" and
    "send a message to your professor" are different decisions."""
    found: list[str] = []
    for key in ("to", "recipient", "recipients", "channel", "cc", "bcc"):
        value = args.get(key)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(str(v) for v in value)
    return found


__all__ = ["Proposal", "SimulationPreview", "ToolGateway", "Risk", "Forbidden"]
