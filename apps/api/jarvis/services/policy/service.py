"""The policy engine.

Deterministic, and outside the agent. The model proposes a structured action; this returns
ALLOW / REQUIRE_APPROVAL / DENY; the executor then **revalidates independently** before
dispatch (blueprint §2). Two checks, because one can be bypassed by whatever bug lets a
proposal reach the executor without passing here.

Nothing in this module calls an LLM, and nothing in it reads provider content. Its inputs
are the proposal, the user, and the device — all facts we control.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.db.models.agent import Action, Approval, Risk
from jarvis.db.models.ops import Device, StandingPermission
from jarvis.services.policy.rules import POLICY_VERSION, ToolRule, manifest_for, rule_for
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)


class Decision(enum.StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    risk: Risk
    reason: str
    policy_version: int = POLICY_VERSION
    requires_local_confirmation: bool = False
    failed_conditions: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


@dataclass(frozen=True)
class ProposalContext:
    """Everything the decision depends on, gathered before evaluating.

    Passed explicitly rather than fetched inside the evaluator, so the rules can be
    tested as pure functions over a context a test can construct by hand.
    """

    user_id: uuid.UUID
    tool: str
    args: dict[str, Any]
    device: Device | None = None
    # True when the proposal was derived from provider content — email, a web page, an
    # LMS object. Such content is data; it can propose no tool call.
    from_untrusted_source: bool = False
    standing_permissions: list[StandingPermission] = field(default_factory=list)


class PolicyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def evaluate(self, context: ProposalContext) -> PolicyResult:
        settings = get_settings()
        rule = rule_for(context.tool)

        # ── R4: denied unconditionally, before anything else is considered ──
        if rule.risk is Risk.R4:
            return PolicyResult(
                Decision.DENY, rule.risk,
                f"{context.tool} is prohibited ({rule.description})",
            )

        # ── an action originating in untrusted content is R4 by definition ──
        # Blueprint §9 lists "arbitrary command from email" as prohibited. Retrieved text
        # may inform a summary; it may never be the reason a tool runs.
        if context.from_untrusted_source and rule.risk is not Risk.R0:
            return PolicyResult(
                Decision.DENY, Risk.R4,
                "Effectful actions cannot originate in retrieved content. "
                "The message was read as data, not as instructions.",
            )

        # ── the kill switch ──
        if settings.global_pause and rule.risk is not Risk.R0:
            return PolicyResult(
                Decision.DENY, rule.risk, "JARVIS is paused; new R1-R3 actions are refused"
            )

        # ── an action nobody can verify is not dispatchable ──
        manifest = manifest_for(context.tool)
        if rule.risk is not Risk.R0 and (manifest is None or not manifest.verify):
            return PolicyResult(
                Decision.DENY, rule.risk,
                f"{context.tool} declares no verifiable evidence, so success could not be proven",
            )
        if manifest is not None:
            missing = [a for a in manifest.args_required if a not in context.args]
            if missing:
                return PolicyResult(
                    Decision.DENY, rule.risk,
                    f"{context.tool} is missing required argument(s): {', '.join(missing)}",
                )

        # ── conditions on the arguments, not merely on the tool name ──
        failed = self._failed_conditions(rule, context)
        if failed:
            return PolicyResult(
                Decision.DENY, rule.risk,
                f"Conditions not met: {', '.join(failed)}",
                failed_conditions=failed,
            )

        # ── the ladder ──
        if rule.risk is Risk.R0:
            return PolicyResult(Decision.ALLOW, rule.risk, "Read-only")

        if rule.risk is Risk.R1:
            return PolicyResult(
                Decision.ALLOW, rule.risk, "Reversible and local, on the paired owner device"
            )

        if rule.risk is Risk.R2:
            if self._standing_permission_covers(rule, context):
                return PolicyResult(
                    Decision.ALLOW, rule.risk, "Covered by a standing permission the user granted"
                )
            return PolicyResult(
                Decision.REQUIRE_APPROVAL, rule.risk,
                f"{rule.description} has an effect outside this machine",
            )

        # R3 — approval is necessary but not sufficient; the Mac must confirm locally too.
        return PolicyResult(
            Decision.REQUIRE_APPROVAL, rule.risk,
            f"{rule.description} is destructive or privileged",
            requires_local_confirmation=True,
        )

    def _failed_conditions(self, rule: ToolRule, context: ProposalContext) -> list[str]:
        failed: list[str] = []
        for condition in rule.conditions:
            if not self._check(condition, context):
                failed.append(condition)
        return failed

    def _check(self, condition: str, context: ProposalContext) -> bool:
        match condition:
            case "device_is_paired_owner":
                return (
                    context.device is not None
                    and context.device.is_active
                    and context.device.user_id == context.user_id
                    and context.device.paired_at is not None
                )
            case "bundle_id_in_allowlist":
                # Checked here *and* again at the helper. The helper's copy is the one
                # that matters if this service is ever wrong.
                bundle = context.args.get("bundle_id")
                allowed = context.device.allowed_bundle_ids if context.device else []
                return bool(bundle) and bundle in allowed
            case "url_scheme_is_https":
                url = str(context.args.get("url", ""))
                return url.startswith("https://")
            case "template_is_registered":
                from jarvis.services.tool_gateway.templates import COMMAND_TEMPLATES

                return context.args.get("template") in COMMAND_TEMPLATES
            case "path_in_scoped_directory":
                return bool(context.args.get("scope_bookmark"))
            case _:
                # An unknown condition fails closed. A typo in a rule must not widen it.
                log.error("policy_unknown_condition", condition=condition)
                return False

    def _standing_permission_covers(self, rule: ToolRule, context: ProposalContext) -> bool:
        """A pre-granted allowance, so routine work does not ask every single time.

        Never applies to R3 or R4: those are exactly the actions a blanket grant should
        not be able to cover.
        """
        if not rule.standing_permission_allowed or rule.risk in (Risk.R3, Risk.R4):
            return False
        now = datetime.now(UTC)
        for permission in context.standing_permissions:
            if permission.tool != context.tool or permission.revoked_at is not None:
                continue
            if permission.expires_at is not None and permission.expires_at <= now:
                continue
            if _risk_rank(permission.max_risk) < _risk_rank(rule.risk.value):
                continue
            if all(context.args.get(k) == v for k, v in (permission.conditions or {}).items()):
                return True
        return False

    async def load_context(
        self,
        user_id: uuid.UUID,
        tool: str,
        args: dict[str, Any],
        *,
        device_id: uuid.UUID | None = None,
        from_untrusted_source: bool = False,
    ) -> ProposalContext:
        device = None
        if device_id is not None:
            device = await self.session.scalar(
                select(Device).where(Device.id == device_id, Device.user_id == user_id)
            )
        permissions = list(
            (
                await self.session.scalars(
                    select(StandingPermission).where(
                        StandingPermission.user_id == user_id,
                        StandingPermission.tool == tool,
                        StandingPermission.revoked_at.is_(None),
                    )
                )
            ).all()
        )
        return ProposalContext(
            user_id=user_id,
            tool=tool,
            args=args,
            device=device,
            from_untrusted_source=from_untrusted_source,
            standing_permissions=permissions,
        )

    # ── the executor's independent second check ────────────────────────
    async def revalidate_for_dispatch(self, action: Action) -> PolicyResult:
        """Re-run the decision at dispatch time, against the row as it now stands.

        Deliberately *not* a cached verdict. Between proposal and dispatch the kill switch
        may have been thrown, the approval may have expired, the device may have been
        revoked, or the args may have been tampered with. Each of those must stop the
        action here even if it passed a moment ago.
        """
        now = datetime.now(UTC)
        if action.expires_at <= now:
            return PolicyResult(Decision.DENY, Risk(action.risk), "Action expired before dispatch")

        context = await self.load_context(
            action.user_id, action.tool, action.args, device_id=action.device_id
        )
        result = await self.evaluate(context)

        if result.decision is Decision.DENY:
            return result

        if result.decision is Decision.REQUIRE_APPROVAL:
            approval = await self.session.scalar(
                select(Approval)
                .where(Approval.action_id == action.id)
                .execution_options(populate_existing=True)
            )
            if approval is None or approval.decision != "approved":
                return PolicyResult(
                    Decision.DENY, result.risk, "No valid approval for this action"
                )
            if approval.expires_at <= now:
                return PolicyResult(Decision.DENY, result.risk, "Approval expired")
            if approval.requires_local_confirmation and approval.local_confirmed_at is None:
                return PolicyResult(
                    Decision.DENY, result.risk,
                    "R3 also requires a local confirmation on the Mac",
                )

            from jarvis.core.security import approval_payload_hash

            expected = approval_payload_hash(
                tool=action.tool,
                args=action.args,
                user_id=str(action.user_id),
                device_id=str(action.device_id) if action.device_id else None,
                expires_at=action.expires_at,
            )
            if expected != approval.payload_hash:
                # The approved payload and the dispatched payload differ. That is either
                # tampering or a bug, and both must stop here.
                log.error(
                    "approval_hash_mismatch",
                    action_id=str(action.id), tool=action.tool,
                )
                return PolicyResult(
                    Decision.DENY, result.risk,
                    "The action no longer matches what was approved",
                )

        return PolicyResult(
            Decision.ALLOW, result.risk, "Revalidated at dispatch",
            requires_local_confirmation=result.requires_local_confirmation,
        )


_RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}


def _risk_rank(risk: str) -> int:
    return _RISK_ORDER.get(risk, 4)
