"""The Mac settings verb and the phone's new hands (PLAN.md 10.4.1–2): allowlisted keys,
re-read state as evidence, and the policy conditions that guard them."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.services.device import JobEnvelope, generate_keypair, sign
from jarvis.services.evidence.verifier import EvidenceRequirement, check_requirement
from jarvis.services.policy.rules import bind_expected, manifest_for
from jarvis.services.tool_gateway import ToolGateway
from macnode.adapters import FakeMacAdapter
from macnode.executor import Executor
from macnode.guard import JobGuard, LocalPolicy


@pytest.fixture
def server_keys():
    return generate_keypair()


@pytest.fixture
def device_keys():
    return generate_keypair()


def _envelope(server_private, action: str, args: dict) -> JobEnvelope:
    base = {
        "job_id": f"job_{uuid.uuid4().hex[:6]}",
        "action": action,
        "args": args,
        "risk": "R1",
        "nonce": uuid.uuid4().hex,
        "issued_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "policy_version": 1,
        "device_id": "device-1",
    }
    envelope = JobEnvelope(**base)
    return JobEnvelope(**{**base, "signature": sign(server_private, envelope.signing_payload())})


def _executor(server_public, device_private, adapter):
    return Executor(
        adapter=adapter,
        guard=JobGuard(server_public_pem=server_public, policy=LocalPolicy()),
        device_private_pem=device_private,
        frontmost_timeout=0.2,
    )


def _req(entry: dict) -> EvidenceRequirement:
    return EvidenceRequirement(kind=entry["kind"], value=entry.get("value"))


def _verdict(entry: dict, observed: dict) -> str:
    return check_requirement(_req(entry), observed).verdict.value


def test_set_setting_reads_the_state_back(server_keys, device_keys):
    sp, spub = server_keys
    dp, _ = device_keys
    adapter = FakeMacAdapter()
    ex = _executor(spub, dp, adapter)
    result = ex.handle(_envelope(sp, "mac.set_setting", {"key": "wifi", "value": "off"}))
    assert result.status == "completed"
    assert result.observed == {"status": 200, "key": "wifi", "value": "off", "state": "off"}
    assert adapter.settings == {"wifi": "off"}


def test_unknown_keys_and_values_are_refused_at_the_helper(server_keys, device_keys):
    sp, spub = server_keys
    dp, _ = device_keys
    ex = _executor(spub, dp, FakeMacAdapter())
    # The guard refuses a key that is not on this Mac's allowlist before anything runs.
    refused = ex.handle(_envelope(sp, "mac.set_setting", {"key": "firewall", "value": "off"}))
    assert refused.status == "rejected"
    bad_value = ex.handle(_envelope(sp, "mac.set_setting", {"key": "wifi", "value": "maybe"}))
    assert bad_value.observed["status"] == 400


def test_a_mac_without_the_tool_reports_inconclusive_not_success(server_keys, device_keys):
    sp, spub = server_keys
    dp, _ = device_keys
    adapter = FakeMacAdapter(unavailable_settings={"bluetooth"})
    result = _executor(spub, dp, adapter).handle(
        _envelope(sp, "mac.set_setting", {"key": "bluetooth", "value": "on"})
    )
    assert result.observed["status"] == 501 and "state" not in result.observed
    expected = bind_expected(manifest_for("mac.set_setting").verify, {"value": "on"})[0]
    assert _verdict(expected, result.observed) == "inconclusive"


def test_the_evidence_is_the_re_read_state():
    expected = bind_expected(manifest_for("mac.set_setting").verify, {"value": "on"})[0]
    assert _verdict(expected, {"status": 200, "state": "on"}) == "verified"
    assert _verdict(expected, {"status": 200, "state": "off"}) == "failed"
    level = bind_expected(manifest_for("mac.set_setting").verify, {"value": "70"})[0]
    assert _verdict(level, {"status": 200, "state": "69"}) == "verified"


# ── the server's gate for the new hands ───────────────────────────────
@pytest.fixture
async def user(session):
    from jarvis.services.identity import IdentityService

    u = await IdentityService(session).register("hands@example.com", "correct-horse-battery")
    await session.commit()
    return u


@pytest.fixture
async def phone(session, user, device_keys):
    from jarvis.services.device import DeviceService
    from jarvis.services.device.keys import sign

    private_pem, public_pem = device_keys
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(
        user.id, name="Pixel", platform="android", public_key_pem=public_pem
    )
    await session.commit()
    device = await devices.complete_pairing(
        user.id,
        challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await session.commit()
    return device


async def test_the_policy_allowlists_settings_panels_and_deeplinks(session, user, phone):
    gateway = ToolGateway(session)

    ok = await gateway.propose(
        user.id, tool="phone.open_settings", args={"panel": "wifi"}, device_id=phone.id
    )
    assert ok.policy.decision.value == "allow" and ok.policy.risk.value == "R1"
    bad = await gateway.propose(
        user.id, tool="phone.open_settings", args={"panel": "developer"}, device_id=phone.id
    )
    assert bad.policy.decision.value == "deny"

    link = await gateway.propose(
        user.id, tool="phone.open_deeplink", args={"url": "spotify:track:1"}, device_id=phone.id
    )
    assert link.policy.decision.value == "allow"
    evil = await gateway.propose(
        user.id, tool="phone.open_deeplink", args={"url": "file:///etc/passwd"}, device_id=phone.id
    )
    assert evil.policy.decision.value == "deny"

    call = await gateway.propose(
        user.id, tool="phone.call", args={"number": "+919999999999"}, device_id=phone.id
    )
    assert call.needs_approval and call.policy.risk.value == "R2"  # it places the call now

    mac_bad = await gateway.propose(
        user.id,
        tool="mac.set_setting",
        args={"key": "firewall", "value": "off"},
        device_id=phone.id,
    )
    assert mac_bad.policy.decision.value == "deny"
