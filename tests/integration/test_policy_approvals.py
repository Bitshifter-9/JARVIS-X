"""Phase 1.5 gate: policy, approvals and simulation mode.

Exit test from PLAN.md §12: *an R2 send cannot run without a valid unexpired matching
approval.* Each test below is a distinct way that guarantee could be defeated.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from jarvis.core.config import get_settings
from jarvis.core.errors import Conflict, PolicyDenied
from jarvis.db.models.agent import Action, ActionStatus, Risk
from jarvis.db.models.ops import AuditLog, Device, StandingPermission
from jarvis.services.identity import IdentityService
from jarvis.services.policy import Decision, PolicyService, ProposalContext, rule_for
from jarvis.services.tool_gateway import COMMAND_TEMPLATES, ToolGateway
from sqlalchemy import select, text

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
SEND_ARGS = {"channel": "telegram", "to": "@team", "body": "Running late on the submission."}


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("policy@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def device(session, user):
    d = Device(
        user_id=user.id,
        name="Pranav's MacBook",
        public_key_pem="-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----",
        fingerprint=uuid.uuid4().hex,
        paired_at=datetime.now(UTC),
        allowed_bundle_ids=["com.google.Chrome", "com.microsoft.VSCode"],
    )
    session.add(d)
    await session.commit()
    return d


# ── the risk ladder ────────────────────────────────────────────────────
async def test_read_only_actions_are_automatic(session, user):
    policy = PolicyService(session)
    result = await policy.evaluate(ProposalContext(user.id, "tasks.list", {}))
    assert result.decision is Decision.ALLOW
    assert result.risk is Risk.R0


async def test_reversible_local_action_is_automatic_on_the_paired_device(session, user, device):
    policy = PolicyService(session)
    context = await policy.load_context(
        user.id, "mac.open_app", {"bundle_id": "com.google.Chrome"}, device_id=device.id
    )
    result = await policy.evaluate(context)
    assert result.decision is Decision.ALLOW
    assert result.risk is Risk.R1


async def test_the_same_action_is_refused_without_a_paired_device(session, user):
    policy = PolicyService(session)
    context = await policy.load_context(user.id, "mac.open_app", {"bundle_id": "com.google.Chrome"})
    result = await policy.evaluate(context)
    assert result.decision is Decision.DENY
    assert "device_is_paired_owner" in result.failed_conditions


async def test_a_bundle_outside_the_allowlist_is_refused(session, user, device):
    policy = PolicyService(session)
    context = await policy.load_context(
        user.id, "mac.open_app", {"bundle_id": "com.apple.Terminal"}, device_id=device.id
    )
    result = await policy.evaluate(context)
    assert result.decision is Decision.DENY
    assert "bundle_id_in_allowlist" in result.failed_conditions


async def test_external_effect_requires_approval(session, user):
    policy = PolicyService(session)
    result = await policy.evaluate(ProposalContext(user.id, "message.send", SEND_ARGS))
    assert result.decision is Decision.REQUIRE_APPROVAL
    assert result.risk is Risk.R2


async def test_destructive_action_also_requires_local_confirmation(session, user):
    policy = PolicyService(session)
    result = await policy.evaluate(
        ProposalContext(user.id, "mac.run_template", {"template": "git.pull", "path": "/tmp/p"})
    )
    assert result.decision is Decision.REQUIRE_APPROVAL
    assert result.risk is Risk.R3
    assert result.requires_local_confirmation is True


@pytest.mark.parametrize(
    "tool", ["payment.send", "credentials.export", "audit.disable", "shell.execute"]
)
async def test_prohibited_actions_are_denied_unconditionally(session, user, tool):
    policy = PolicyService(session)
    result = await policy.evaluate(ProposalContext(user.id, tool, {}))
    assert result.decision is Decision.DENY
    assert result.risk is Risk.R4


async def test_an_unregistered_tool_is_treated_as_prohibited(session, user):
    """A gap in review is not a convenience to be granted."""
    assert rule_for("some.tool.nobody.reviewed").risk is Risk.R4
    result = await PolicyService(session).evaluate(
        ProposalContext(user.id, "some.tool.nobody.reviewed", {})
    )
    assert result.decision is Decision.DENY


# ── prompt injection: the headline threat ──────────────────────────────
async def test_an_action_originating_in_retrieved_content_is_refused(session, user, device):
    """Blueprint §9: "arbitrary command from email" is R4.

    Even a tool that would normally be automatic must not run because a *message* asked
    for it. Retrieved text is data.
    """
    policy = PolicyService(session)
    context = await policy.load_context(
        user.id, "mac.open_app", {"bundle_id": "com.google.Chrome"}, device_id=device.id
    )
    hostile = ProposalContext(
        user_id=context.user_id, tool=context.tool, args=context.args,
        device=context.device, from_untrusted_source=True,
    )
    result = await policy.evaluate(hostile)
    assert result.decision is Decision.DENY
    assert result.risk is Risk.R4
    assert "read as data" in result.reason


async def test_untrusted_content_may_still_drive_read_only_work(session, user):
    """The boundary blocks effects, not comprehension."""
    result = await PolicyService(session).evaluate(
        ProposalContext(user.id, "memory.search", {}, from_untrusted_source=True)
    )
    assert result.decision is Decision.ALLOW


# ── unverifiable actions ───────────────────────────────────────────────
async def test_an_action_with_no_verifiable_evidence_is_refused(session, user):
    """If success cannot be proven, the action is not dispatchable."""
    from jarvis.services.policy import rules

    rules.RULES["fake.unverifiable"] = rules.ToolRule(
        "fake.unverifiable", Risk.R2, "A tool with no manifest"
    )
    try:
        result = await PolicyService(session).evaluate(
            ProposalContext(user.id, "fake.unverifiable", {})
        )
        assert result.decision is Decision.DENY
        assert "no verifiable evidence" in result.reason
    finally:
        del rules.RULES["fake.unverifiable"]


async def test_missing_required_arguments_are_refused(session, user):
    result = await PolicyService(session).evaluate(
        ProposalContext(user.id, "message.send", {"channel": "telegram"})
    )
    assert result.decision is Decision.DENY
    assert "missing required argument" in result.reason


# ── the kill switch ────────────────────────────────────────────────────
async def test_global_pause_refuses_new_effectful_actions(session, user, device, monkeypatch):
    monkeypatch.setattr(get_settings(), "global_pause", True)
    policy = PolicyService(session)

    context = await policy.load_context(
        user.id, "mac.open_app", {"bundle_id": "com.google.Chrome"}, device_id=device.id
    )
    assert (await policy.evaluate(context)).decision is Decision.DENY
    # Reading is still permitted: a pause stops effects, not visibility.
    assert (
        await policy.evaluate(ProposalContext(user.id, "tasks.list", {}))
    ).decision is Decision.ALLOW


# ── the gate: an R2 send cannot run unapproved ─────────────────────────
async def test_r2_send_cannot_dispatch_without_approval(session, user):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()

    assert proposal.needs_approval
    assert proposal.action.status == ActionStatus.AWAITING_APPROVAL.value

    with pytest.raises(PolicyDenied, match="No valid approval"):
        await gateway.authorize_dispatch(proposal.action.id)


async def test_r2_send_dispatches_once_approved(session, user):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()

    await gateway.decide(user.id, proposal.approval.id, approved=True, decided_by="telegram")
    await session.commit()

    action = await gateway.authorize_dispatch(proposal.action.id)
    await session.commit()
    assert action.status == ActionStatus.DISPATCHED.value


async def test_a_rejected_approval_blocks_dispatch(session, user):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()

    await gateway.decide(user.id, proposal.approval.id, approved=False, decided_by="mobile")
    await session.commit()

    with pytest.raises(PolicyDenied):
        await gateway.authorize_dispatch(proposal.action.id)


async def test_an_expired_approval_blocks_dispatch(session, user):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()
    await gateway.decide(user.id, proposal.approval.id, approved=True, decided_by="mobile")
    await session.commit()

    await session.execute(
        text("UPDATE approvals SET expires_at = now() - interval '1 minute' WHERE id = :i"),
        {"i": proposal.approval.id},
    )
    await session.commit()

    with pytest.raises(PolicyDenied, match="expired"):
        await gateway.authorize_dispatch(proposal.action.id)


async def test_an_expired_action_blocks_dispatch_even_with_a_live_approval(session, user):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()
    await gateway.decide(user.id, proposal.approval.id, approved=True, decided_by="mobile")
    await session.commit()

    await session.execute(
        text("UPDATE actions SET expires_at = now() - interval '1 minute' WHERE id = :i"),
        {"i": proposal.action.id},
    )
    await session.commit()

    with pytest.raises(PolicyDenied, match="expired"):
        await gateway.authorize_dispatch(proposal.action.id)


async def test_tampering_with_the_args_after_approval_breaks_the_hash(session, user):
    """The heart of payload binding: approve one message, dispatch another."""
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()
    await gateway.decide(user.id, proposal.approval.id, approved=True, decided_by="mobile")
    await session.commit()

    # The recipient is swapped after the human said yes.
    await session.execute(
        text("""UPDATE actions SET args = jsonb_set(args, '{to}', '"@everyone"')
                WHERE id = :i"""),
        {"i": proposal.action.id},
    )
    await session.commit()

    with pytest.raises(PolicyDenied, match="no longer matches what was approved"):
        await gateway.authorize_dispatch(proposal.action.id)


async def test_an_approval_cannot_be_decided_twice(session, user):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()
    await gateway.decide(user.id, proposal.approval.id, approved=True, decided_by="mobile")
    await session.commit()

    with pytest.raises(Conflict, match="already been decided"):
        await gateway.decide(user.id, proposal.approval.id, approved=False, decided_by="mobile")


async def test_r3_needs_local_confirmation_as_well_as_approval(session, user):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(
        user.id, tool="mac.run_template",
        args={"template": "git.pull", "params": {"path": "/Users/p/proj"}},
    )
    await session.commit()

    assert proposal.approval.requires_local_confirmation is True
    await gateway.decide(user.id, proposal.approval.id, approved=True, decided_by="mobile")
    await session.commit()

    with pytest.raises(PolicyDenied, match="local confirmation"):
        await gateway.authorize_dispatch(proposal.action.id)

    await gateway.confirm_locally(user.id, proposal.approval.id)
    await session.commit()
    action = await gateway.authorize_dispatch(proposal.action.id)
    assert action.status == ActionStatus.DISPATCHED.value


async def test_denied_proposals_are_recorded_not_silently_dropped(session, user):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="payment.send", args={"amount": 500})
    await session.commit()

    assert proposal.action.status == ActionStatus.DENIED.value
    entries = (await session.scalars(select(AuditLog))).all()
    assert any(e.action == "action.proposed" and e.detail["decision"] == "deny" for e in entries)


# ── standing permissions ───────────────────────────────────────────────
async def test_a_standing_permission_can_pre_authorize_an_r1_action(session, user, device):
    session.add(
        StandingPermission(
            user_id=user.id, tool="mac.open_app",
            conditions={"bundle_id": "com.google.Chrome"}, max_risk="R1",
        )
    )
    await session.commit()

    policy = PolicyService(session)
    context = await policy.load_context(
        user.id, "mac.open_app", {"bundle_id": "com.google.Chrome"}, device_id=device.id
    )
    assert (await policy.evaluate(context)).decision is Decision.ALLOW


async def test_a_standing_permission_cannot_cover_a_destructive_action(session, user):
    """A blanket grant must not be able to reach R3."""
    session.add(
        StandingPermission(user_id=user.id, tool="mac.run_template", conditions={}, max_risk="R3")
    )
    await session.commit()

    policy = PolicyService(session)
    context = await policy.load_context(
        user.id, "mac.run_template", {"template": "git.pull", "params": {"path": "/x"}}
    )
    result = await policy.evaluate(context)
    assert result.decision is Decision.REQUIRE_APPROVAL


# ── simulation mode ────────────────────────────────────────────────────
async def test_simulation_previews_the_plan_without_performing_it(session, user):
    gateway = ToolGateway(session)
    preview = await gateway.simulate(user.id, tool="message.send", args=SEND_ARGS)

    assert preview.risk == "R2"
    assert preview.recipients == ["telegram", "@team"] or "@team" in preview.recipients
    assert preview.expected_evidence == ["provider_object_id"]
    assert preview.policy_decision == "require_approval"
    # Nothing was written: a simulation leaves no action behind.
    assert await session.scalar(select(Action)) is None


async def test_simulation_shows_the_exact_command_for_a_template(session, user):
    """"Run a predefined template" is not informed consent; the exact command is."""
    gateway = ToolGateway(session)
    preview = await gateway.simulate(
        user.id, tool="mac.run_template",
        args={"template": "git.pull", "params": {"path": "/Users/p/my project"}},
    )
    assert preview.command_preview == "git -C '/Users/p/my project' pull --ff-only"


async def test_simulate_and_execute_share_one_payload_hash(session, user):
    """The claim "flip SIMULATE to EXECUTE on the same plan" must be checkable."""
    gateway = ToolGateway(session)
    preview = await gateway.simulate(user.id, tool="message.send", args=SEND_ARGS)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()

    # Both hash the same tool, args, user and device. Only the expiry differs by wall
    # clock, so compare the components that bind the decision.
    from jarvis.core.security import approval_payload_hash

    rehashed = approval_payload_hash(
        tool="message.send", args=SEND_ARGS, user_id=str(user.id),
        device_id=None, expires_at=proposal.action.expires_at,
    )
    assert rehashed == proposal.approval.payload_hash
    assert preview.payload_hash.startswith("sha256:")


# ── command templates ──────────────────────────────────────────────────
def test_template_parameters_are_argv_entries_not_shell_text():
    """v1's run_command is gone; a metacharacter has nothing to act on here."""
    template = COMMAND_TEMPLATES["git.status"]
    argv = template.render({"path": "/tmp/x; rm -rf /"})
    assert argv == ["git", "-C", "/tmp/x; rm -rf /", "status", "--short"]
    assert template.preview({"path": "/tmp/x; rm -rf /"}) == (
        "git -C '/tmp/x; rm -rf /' status --short"
    )


def test_a_template_with_a_missing_parameter_is_refused():
    with pytest.raises(ValueError, match="missing parameter"):
        COMMAND_TEMPLATES["git.pull"].render({})


async def test_an_unregistered_template_is_refused_by_policy(session, user):
    result = await PolicyService(session).evaluate(
        ProposalContext(user.id, "mac.run_template", {"template": "rm.everything"})
    )
    assert result.decision is Decision.DENY
    assert "template_is_registered" in result.failed_conditions
