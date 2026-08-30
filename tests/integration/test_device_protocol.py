"""Phase 1.8 and 1.9 gates: the Mac node and the signed job protocol.

Exit tests from PLAN.md §12:
* 1.8 — Chrome opens; evidence shows pid and frontmost bundle; a non-allowlisted bundle
  is refused **at the helper**.
* 1.9 — replayed and expired jobs are rejected and audited; an offline job is offered for
  review on reconnect.

The macOS layer is faked so the helper's *logic* is tested anywhere; the fake is scripted
to reproduce the awkward real-world cases (an app that launches but will not come
forward, an app that is not installed).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.core.errors import Forbidden
from jarvis.db.models.agent import ActionStatus, Evidence, Verdict
from jarvis.db.models.ops import AuditLog, Device
from jarvis.services.device import (
    DeviceService,
    JobEnvelope,
    RejectReason,
    generate_keypair,
    sign,
)
from jarvis.services.evidence import EvidenceService
from jarvis.services.identity import IdentityService
from jarvis.services.tool_gateway import ToolGateway
from macnode.adapters import FakeMacAdapter
from macnode.executor import Executor
from macnode.guard import JobGuard, LocalPolicy, NonceLedger
from sqlalchemy import select, text

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
CHROME = "com.google.Chrome"
TERMINAL = "com.apple.Terminal"


@pytest.fixture
def server_keys():
    return generate_keypair()


@pytest.fixture
def device_keys():
    return generate_keypair()


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("mac@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def paired(session, user, device_keys):
    """A fully paired Mac: key registered, challenge signed, pairing complete."""
    private_pem, public_pem = device_keys
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(
        user.id, name="Pranav's MacBook", public_key_pem=public_pem,
        allowed_bundle_ids=[CHROME, "com.microsoft.VSCode"],
        capabilities=["mac.open_app", "mac.focus_app"],
    )
    await session.commit()
    device = await devices.complete_pairing(
        user.id,
        challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await session.commit()
    return device


# ── 1.9 pairing ────────────────────────────────────────────────────────
async def test_pairing_requires_proving_possession_of_the_private_key(session, user, device_keys):
    """Registering a public key must not be enough, or anyone could claim a pairing."""
    _private, public_pem = device_keys
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(user.id, name="Mac", public_key_pem=public_pem)
    await session.commit()

    attacker_private, _ = generate_keypair()
    with pytest.raises(Forbidden, match="signature did not verify"):
        await devices.complete_pairing(
            user.id, challenge=challenge.challenge,
            signature=sign(attacker_private, challenge.challenge.encode()),
        )


async def test_a_pairing_challenge_is_single_use(session, user, device_keys):
    private_pem, public_pem = device_keys
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(user.id, name="Mac", public_key_pem=public_pem)
    await session.commit()
    signature = sign(private_pem, challenge.challenge.encode())

    await devices.complete_pairing(user.id, challenge=challenge.challenge, signature=signature)
    await session.commit()

    with pytest.raises(Forbidden, match="already-used"):
        await devices.complete_pairing(user.id, challenge=challenge.challenge, signature=signature)


async def test_pairing_is_audited(session, user, paired):
    entries = (await session.scalars(select(AuditLog))).all()
    assert any(e.action == "device.paired" for e in entries)


async def test_a_device_key_cannot_be_claimed_by_a_second_account(session, user, device_keys):
    from jarvis.core.errors import Conflict

    _private, public_pem = device_keys
    devices = DeviceService(session)
    await devices.begin_pairing(user.id, name="Mac", public_key_pem=public_pem)
    await session.commit()

    intruder = await IdentityService(session).register("intruder@example.com", PASSWORD)
    await session.commit()
    with pytest.raises(Conflict, match="another account"):
        await devices.begin_pairing(intruder.id, name="Mine now", public_key_pem=public_pem)


async def test_revoking_a_device_stops_it_connecting(session, user, paired):
    devices = DeviceService(session)
    await devices.revoke(user.id, paired.id, reason="lost laptop")
    await session.commit()

    with pytest.raises(Forbidden, match="revoked"):
        await devices.connect(paired.id, "conn-1")

    assert (await session.get(Device, paired.id)).is_active is False


# ── 1.9 the signed job envelope ────────────────────────────────────────
async def _dispatchable_action(session, user, device, tool="mac.open_app", bundle=CHROME):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(
        user.id, tool=tool, args={"bundle_id": bundle}, device_id=device.id
    )
    await session.commit()
    action = await gateway.authorize_dispatch(proposal.action.id)
    await session.commit()
    return action


async def test_a_dispatched_job_is_signed_by_the_server(session, user, paired, server_keys):
    server_private, server_public = server_keys
    action = await _dispatchable_action(session, user, paired)

    envelope = await DeviceService(session).build_envelope(
        action, server_private_pem=server_private
    )
    await session.commit()

    assert envelope.signature
    assert envelope.action == "mac.open_app"
    assert envelope.device_id == str(paired.id)

    from jarvis.services.device.keys import verify

    assert verify(server_public, envelope.signing_payload(), envelope.signature)


def test_the_signature_covers_every_field_that_matters(server_keys):
    """Tampering with any signed field must invalidate the envelope."""
    server_private, server_public = server_keys
    base = JobEnvelope(
        job_id="job_1", action="mac.open_app", args={"bundle_id": CHROME}, risk="R1",
        nonce="n1", issued_at=datetime.now(UTC).isoformat(),
        expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        policy_version=1, device_id=str(uuid.uuid4()),
    )
    signature = sign(server_private, base.signing_payload())
    guard = JobGuard(
        server_public_pem=server_public,
        policy=LocalPolicy(allowed_bundle_ids={CHROME, TERMINAL}),
    )

    good = JobEnvelope(**{**base.__dict__, "signature": signature})
    assert guard.admit(good).accepted

    for field, value in [
        ("action", "mac.run_template"),
        ("args", {"bundle_id": TERMINAL}),
        ("risk", "R3"),
        ("policy_version", 99),
        ("device_id", str(uuid.uuid4())),
    ]:
        tampered = JobEnvelope(**{**base.__dict__, field: value, "signature": signature})
        verdict = JobGuard(
            server_public_pem=server_public,
            policy=LocalPolicy(allowed_bundle_ids={CHROME, TERMINAL}),
        ).admit(tampered)
        assert not verdict.accepted, f"tampering with {field} was not detected"
        assert verdict.reason is RejectReason.BAD_SIGNATURE


def test_an_unsigned_job_is_refused(server_keys):
    _private, server_public = server_keys
    guard = JobGuard(server_public_pem=server_public, policy=LocalPolicy())
    envelope = JobEnvelope(
        job_id="j", action="mac.open_app", args={"bundle_id": CHROME}, risk="R1",
        nonce="n", issued_at=datetime.now(UTC).isoformat(),
        expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        policy_version=1, device_id="d", signature="",
    )
    assert guard.admit(envelope).reason is RejectReason.BAD_SIGNATURE


def _envelope(server_private, **overrides) -> JobEnvelope:
    base = {
        "job_id": "job_1", "action": "mac.open_app", "args": {"bundle_id": CHROME},
        "risk": "R1", "nonce": "nonce-1", "issued_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "policy_version": 1, "device_id": "device-1",
    }
    base.update(overrides)
    envelope = JobEnvelope(**base)
    return JobEnvelope(**{**base, "signature": sign(server_private, envelope.signing_payload())})


def test_a_replayed_job_is_rejected(server_keys):
    """A captured job must not run twice."""
    server_private, server_public = server_keys
    guard = JobGuard(
        server_public_pem=server_public, policy=LocalPolicy(allowed_bundle_ids={CHROME})
    )
    envelope = _envelope(server_private)

    assert guard.admit(envelope).accepted
    replay = guard.admit(envelope)
    assert not replay.accepted
    assert replay.reason is RejectReason.REPLAYED_NONCE


def test_an_expired_job_is_rejected(server_keys):
    server_private, server_public = server_keys
    guard = JobGuard(
        server_public_pem=server_public, policy=LocalPolicy(allowed_bundle_ids={CHROME})
    )
    stale = _envelope(
        server_private, expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    )
    verdict = guard.admit(stale)
    assert not verdict.accepted
    assert verdict.reason is RejectReason.EXPIRED


def test_a_rejected_job_does_not_burn_its_nonce(server_keys):
    """Otherwise a transient rejection would permanently block a legitimate retry."""
    server_private, server_public = server_keys
    guard = JobGuard(
        server_public_pem=server_public,
        policy=LocalPolicy(allowed_bundle_ids=set()),  # nothing allowlisted yet
    )
    envelope = _envelope(server_private)
    assert guard.admit(envelope).reason is RejectReason.NOT_ALLOWLISTED

    guard.policy.allowed_bundle_ids = {CHROME}
    assert guard.admit(envelope).accepted


def test_the_stop_button_outranks_a_valid_signature(server_keys):
    server_private, server_public = server_keys
    policy = LocalPolicy(allowed_bundle_ids={CHROME})
    guard = JobGuard(server_public_pem=server_public, policy=policy)
    policy.stopped = True

    verdict = guard.admit(_envelope(server_private))
    assert not verdict.accepted
    assert verdict.reason is RejectReason.STOPPED


def test_the_nonce_ledger_is_bounded():
    """A helper running for months must not accumulate an unbounded set."""
    ledger = NonceLedger(capacity=10)
    for i in range(50):
        ledger.remember(f"n{i}")
    assert len(ledger) == 10
    assert ledger.seen("n49") and not ledger.seen("n0")


# ── 1.8 the helper refuses on its own authority ────────────────────────
def test_a_non_allowlisted_bundle_is_refused_at_the_helper(server_keys):
    """The gate. Even a perfectly signed job from the real server must not open Terminal
    if this Mac's own allowlist does not include it."""
    server_private, server_public = server_keys
    guard = JobGuard(
        server_public_pem=server_public,
        policy=LocalPolicy(allowed_bundle_ids={CHROME}),  # Terminal deliberately absent
    )
    verdict = guard.admit(_envelope(server_private, args={"bundle_id": TERMINAL}))
    assert not verdict.accepted
    assert verdict.reason is RejectReason.NOT_ALLOWLISTED
    assert TERMINAL in verdict.detail


def test_an_action_the_helper_does_not_offer_is_refused(server_keys):
    server_private, server_public = server_keys
    guard = JobGuard(
        server_public_pem=server_public,
        policy=LocalPolicy(allowed_bundle_ids={CHROME}, allowed_actions={"mac.focus_app"}),
    )
    verdict = guard.admit(_envelope(server_private))
    assert verdict.reason is RejectReason.UNKNOWN_ACTION


# ── 1.8 execution and evidence ─────────────────────────────────────────
def _executor(server_public, device_private, adapter, *, frontmost_timeout=8.0, **policy_kwargs):
    policy = LocalPolicy(allowed_bundle_ids={CHROME}, **policy_kwargs)
    return Executor(
        adapter=adapter,
        guard=JobGuard(server_public_pem=server_public, policy=policy),
        device_private_pem=device_private,
        frontmost_timeout=frontmost_timeout,
    )


def test_opening_an_app_reports_pid_and_frontmost_bundle(server_keys, device_keys):
    server_private, server_public = server_keys
    device_private, _ = device_keys
    adapter = FakeMacAdapter(installed={CHROME})

    result = _executor(server_public, device_private, adapter).handle(_envelope(server_private))

    assert result.status == "completed"
    assert result.observed["pid"] == 4242
    assert result.observed["is_running"] is True
    assert result.observed["frontmost_bundle_id"] == CHROME
    assert adapter.launched == [CHROME]


def test_an_app_that_is_not_installed_reports_that_it_is_not_running(server_keys, device_keys):
    server_private, server_public = server_keys
    device_private, _ = device_keys
    adapter = FakeMacAdapter(installed=set())

    result = _executor(server_public, device_private, adapter).handle(_envelope(server_private))
    assert result.status == "completed"
    assert result.observed["is_running"] is False


def test_an_app_that_launches_but_will_not_come_forward_is_reported_honestly(
    server_keys, device_keys
):
    """The helper reports what it saw. It does not claim success it cannot observe."""
    server_private, server_public = server_keys
    device_private, _ = device_keys
    adapter = FakeMacAdapter(
        installed={CHROME}, refuse_frontmost={CHROME}, frontmost="com.apple.finder"
    )

    executor = _executor(server_public, device_private, adapter, frontmost_timeout=0.01)
    result = executor.handle(_envelope(server_private))

    assert result.status == "completed"
    assert result.observed["is_running"] is True
    assert result.observed["frontmost_bundle_id"] == "com.apple.finder"


def test_results_are_signed_by_the_device(server_keys, device_keys):
    """Otherwise anything reaching the socket could report a fabricated success."""
    server_private, server_public = server_keys
    device_private, device_public = device_keys
    adapter = FakeMacAdapter(installed={CHROME})

    result = _executor(server_public, device_private, adapter).handle(_envelope(server_private))

    from jarvis.services.device.keys import verify

    assert verify(device_public, result.signing_payload(), result.signature)


def test_command_templates_run_without_a_shell(server_keys, device_keys):
    """v1's run_command is gone; a metacharacter has nothing to act on."""
    server_private, server_public = server_keys
    device_private, _ = device_keys
    adapter = FakeMacAdapter(command_result=(0, "clean"))

    executor = _executor(
        server_public, device_private, adapter, allowed_templates={"git.status"}
    )
    envelope = _envelope(
        server_private, action="mac.run_template",
        args={"template": "git.status", "params": {"path": "/tmp/x; rm -rf /"}},
    )
    result = executor.handle(envelope)

    assert result.status == "completed"
    assert adapter.commands == [["git", "-C", "/tmp/x; rm -rf /", "status", "--short"]]


async def test_server_verifies_the_device_signature_before_trusting_a_result(
    session, user, paired, server_keys, device_keys
):
    server_private, server_public = server_keys
    device_private, _ = device_keys
    devices = DeviceService(session)

    action = await _dispatchable_action(session, user, paired)
    envelope = await devices.build_envelope(action, server_private_pem=server_private)
    await session.commit()

    adapter = FakeMacAdapter(installed={CHROME})
    result = _executor(server_public, device_private, adapter).handle(envelope)

    assert await devices.verify_result(paired, result) is True

    from jarvis.services.device.protocol import JobResult

    forged = JobResult(
        job_id=result.job_id, status="completed",
        observed={"frontmost_bundle_id": CHROME}, signature=result.signature,
    )
    assert await devices.verify_result(paired, forged) is False


async def test_the_full_mac_loop_produces_verified_evidence(
    session, user, paired, server_keys, device_keys
):
    """1.8 end to end: propose → dispatch → helper → observation → verified evidence."""
    server_private, server_public = server_keys
    device_private, _ = device_keys

    action = await _dispatchable_action(session, user, paired)
    envelope = await DeviceService(session).build_envelope(
        action, server_private_pem=server_private
    )
    await session.commit()

    adapter = FakeMacAdapter(installed={CHROME})
    result = _executor(server_public, device_private, adapter).handle(envelope)

    verification = await EvidenceService(session).verify(action, result.observed)
    await session.commit()

    assert verification.verdict is Verdict.VERIFIED
    assert action.status == ActionStatus.SUCCEEDED.value

    rows = (await session.scalars(select(Evidence).where(Evidence.action_id == action.id))).all()
    kinds = {r.kind for r in rows}
    assert kinds == {"process_running", "foreground_window_bundle_id"}
    assert all(r.verdict == Verdict.VERIFIED.value for r in rows)


async def test_the_wrong_app_coming_forward_fails_verification(
    session, user, paired, server_keys, device_keys
):
    """Proof the verifier is doing work: a launch that did not surface is not success."""
    server_private, server_public = server_keys
    device_private, _ = device_keys

    action = await _dispatchable_action(session, user, paired)
    envelope = await DeviceService(session).build_envelope(
        action, server_private_pem=server_private
    )
    await session.commit()

    adapter = FakeMacAdapter(
        installed={CHROME}, refuse_frontmost={CHROME}, frontmost="com.apple.Safari"
    )
    executor = _executor(server_public, device_private, adapter, frontmost_timeout=0.01)
    result = executor.handle(envelope)

    verification = await EvidenceService(session).verify(action, result.observed)
    await session.commit()

    assert verification.verdict is Verdict.FAILED
    assert action.status == ActionStatus.FAILED.value


# ── 1.9 offline behaviour ──────────────────────────────────────────────
async def test_a_stale_job_is_offered_for_review_not_run_late(session, user, paired):
    """Blueprint §12: jobs are never silently executed once their intent is stale.

    Both actions are *approved but undelivered* — the state an action sits in while the
    Mac is offline, because dispatch is authorized at the moment of delivery.
    """
    gateway = ToolGateway(session)
    fresh = (
        await gateway.propose(
            user.id, tool="mac.open_app", args={"bundle_id": CHROME}, device_id=paired.id
        )
    ).action
    stale = (
        await gateway.propose(
            user.id, tool="mac.open_app",
            args={"bundle_id": "com.microsoft.VSCode"}, device_id=paired.id,
        )
    ).action
    await session.commit()

    await session.execute(
        text("UPDATE actions SET expires_at = now() - interval '1 hour' WHERE id = :i"),
        {"i": stale.id},
    )
    await session.commit()

    dispatchable, needs_review = await DeviceService(session).pending_for_device(paired.id)
    await session.commit()

    assert [a.id for a in dispatchable] == [fresh.id]
    assert [a.id for a in needs_review] == [stale.id]
    assert (await session.get(type(stale), stale.id)).status == ActionStatus.EXPIRED.value

    entries = (await session.scalars(select(AuditLog))).all()
    assert any(e.action == "action.expired_while_offline" for e in entries)


async def test_connection_state_is_tracked(session, user, paired):
    devices = DeviceService(session)
    assert await devices.is_online(paired.id) is False

    await devices.connect(paired.id, "conn-1")
    await session.commit()
    assert await devices.is_online(paired.id) is True

    await devices.heartbeat(paired.id, "conn-1")
    await devices.disconnect(paired.id, "conn-1")
    await session.commit()
    assert await devices.is_online(paired.id) is False


# ── 3.2 the full Mac tool set ──────────────────────────────────────────
def _ui_executor(server_public, device_private, adapter, **policy):
    return _executor(
        server_public, device_private, adapter,
        allowed_templates={"git.status"}, **policy,
    )


def test_reading_the_ui_reports_the_accessibility_tree(server_keys, device_keys):
    from macnode.adapters import UIElement

    server_private, server_public = server_keys
    device_private, _ = device_keys
    adapter = FakeMacAdapter(
        installed={CHROME},
        elements={CHROME: [
            UIElement(role="AXButton", title="Submit", enabled=True),
            UIElement(role="AXTextField", title="Comment", value="", enabled=True),
        ]},
    )
    adapter.launch(CHROME)

    result = _ui_executor(server_public, device_private, adapter).handle(
        _envelope(server_private, action="mac.read_ui", args={"bundle_id": CHROME})
    )
    assert result.status == "completed"
    assert result.observed["element_count"] == 2
    assert {e["title"] for e in result.observed["elements"]} == {"Submit", "Comment"}


def test_a_revoked_accessibility_permission_is_reported_not_guessed(server_keys, device_keys):
    """Without this the calls simply return nothing, which reads as "the button wasn't there"."""
    server_private, server_public = server_keys
    device_private, _ = device_keys
    adapter = FakeMacAdapter(installed={CHROME}, accessibility=False)

    result = _ui_executor(server_public, device_private, adapter).handle(
        _envelope(server_private, action="mac.read_ui", args={"bundle_id": CHROME})
    )
    assert result.observed["permission"] == "accessibility_denied"
    assert result.observed["elements"] == []


def test_pressing_a_button_that_does_not_exist_reports_failure(server_keys, device_keys):
    from macnode.adapters import UIElement

    server_private, server_public = server_keys
    device_private, _ = device_keys
    adapter = FakeMacAdapter(
        installed={CHROME},
        elements={CHROME: [UIElement(role="AXButton", title="Submit")]},
    )
    adapter.launch(CHROME)
    executor = _ui_executor(server_public, device_private, adapter)

    missing = executor.handle(
        _envelope(server_private, action="mac.press_button",
                  args={"bundle_id": CHROME, "title": "Nonexistent"})
    )
    assert missing.observed["pressed"] is False
    assert adapter.pressed == []

    found = executor.handle(
        _envelope(server_private, nonce="n2", action="mac.press_button",
                  args={"bundle_id": CHROME, "title": "Submit"})
    )
    assert found.observed["pressed"] is True
    assert adapter.pressed == [(CHROME, "Submit")]


def test_a_disabled_control_is_not_pressed(server_keys, device_keys):
    from macnode.adapters import UIElement

    server_private, server_public = server_keys
    device_private, _ = device_keys
    adapter = FakeMacAdapter(
        installed={CHROME},
        elements={CHROME: [UIElement(role="AXButton", title="Submit", enabled=False)]},
    )
    adapter.launch(CHROME)

    result = _ui_executor(server_public, device_private, adapter).handle(
        _envelope(server_private, action="mac.press_button",
                  args={"bundle_id": CHROME, "title": "Submit"})
    )
    assert result.observed["pressed"] is False


def test_a_capture_returns_a_digest_not_the_pixels(server_keys, device_keys):
    """The bytes stay on the Mac; a digest proves the state without shipping a desktop."""
    server_private, server_public = server_keys
    device_private, _ = device_keys
    adapter = FakeMacAdapter(installed={CHROME})
    adapter.launch(CHROME)

    result = _ui_executor(server_public, device_private, adapter).handle(
        _envelope(server_private, action="mac.capture_window", args={"bundle_id": CHROME})
    )
    assert result.observed["digest"].startswith("sha256:")
    assert "image" not in result.observed
    assert "path" not in result.observed


def test_a_denied_screen_recording_permission_is_reported(server_keys, device_keys):
    server_private, server_public = server_keys
    device_private, _ = device_keys
    adapter = FakeMacAdapter(installed={CHROME}, screen_recording=False)
    adapter.launch(CHROME)

    result = _ui_executor(server_public, device_private, adapter).handle(
        _envelope(server_private, action="mac.capture_window", args={"bundle_id": CHROME})
    )
    assert result.observed["permission"] == "screen_recording_denied"
    assert result.observed["digest"] is None


def test_file_access_cannot_escape_its_granted_directory(server_keys, device_keys):
    """No full-disk access: a path outside the chosen directory is refused, not resolved."""
    server_private, server_public = server_keys
    device_private, _ = device_keys
    adapter = FakeMacAdapter(scoped_files={"/Users/p/project/report.pdf"})
    executor = _ui_executor(server_public, device_private, adapter)

    inside = executor.handle(
        _envelope(server_private, action="mac.file_exists",
                  args={"path": "/Users/p/project/report.pdf",
                        "scope_bookmark": "/Users/p/project"})
    )
    assert inside.observed["exists"] is True

    outside = executor.handle(
        _envelope(server_private, nonce="n2", action="mac.file_exists",
                  args={"path": "/Users/p/.ssh/id_rsa", "scope_bookmark": "/Users/p/project"})
    )
    assert outside.observed["exists"] is False


def test_the_helper_refuses_new_tools_it_has_not_enabled(server_keys, device_keys):
    server_private, server_public = server_keys
    device_private, _ = device_keys
    guard = JobGuard(
        server_public_pem=server_public,
        policy=LocalPolicy(allowed_bundle_ids={CHROME}, allowed_actions={"mac.open_app"}),
    )
    verdict = guard.admit(
        _envelope(server_private, action="mac.capture_window", args={"bundle_id": CHROME})
    )
    assert verdict.reason is RejectReason.UNKNOWN_ACTION


def test_new_mac_tools_still_honour_the_bundle_allowlist(server_keys, device_keys):
    server_private, server_public = server_keys
    device_private, _ = device_keys
    guard = JobGuard(
        server_public_pem=server_public, policy=LocalPolicy(allowed_bundle_ids={CHROME})
    )
    for action in ("mac.read_ui", "mac.press_button", "mac.capture_window"):
        verdict = guard.admit(
            _envelope(server_private, nonce=f"n-{action}", action=action,
                      args={"bundle_id": TERMINAL, "title": "x"})
        )
        assert verdict.reason is RejectReason.NOT_ALLOWLISTED, action
