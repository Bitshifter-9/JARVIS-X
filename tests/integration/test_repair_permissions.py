"""The fix loop and standing permissions (PLAN.md 10.9.1–2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.job import Job
from jarvis.db.models.ops import AuditLog, NotificationEndpoint, WorkerHeartbeat
from jarvis.db.models.source import SourceAccount
from jarvis.db.queue import JobQueue
from jarvis.services.identity import IdentityService
from jarvis.services.repair import repair
from jarvis.services.tool_gateway import ToolGateway
from sqlalchemy import select, text

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
SEND_ARGS = {"channel": "telegram", "to": "@team", "body": "Running late on the submission."}


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("repair@example.com", PASSWORD)
    session.add(NotificationEndpoint(user_id=u.id, channel="push", address="tok"))
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    r = await client.post(
        "/v1/auth/login", json={"email": "repair@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def quiet_tell(monkeypatch):
    told: list[list[dict]] = []

    async def fake(session, user_id, fixes):  # noqa: ANN001
        told.append(fixes)

    monkeypatch.setattr("jarvis.services.repair._tell", fake)
    return told


# ── the fix loop ──────────────────────────────────────────────────────
async def test_a_dead_lettered_job_gets_one_second_chance(session, user, quiet_tell):
    queue = JobQueue(session)
    job = await queue.enqueue(
        "agent.run", {"user_id": str(user.id), "text": "x"}, user_id=user.id, max_attempts=1
    )
    await session.commit()
    (claimed,) = await queue.claim("w1", limit=1, kinds=["agent.run"])
    await queue.fail(claimed.id, "boom")
    await session.commit()
    await session.refresh(job)
    assert job.status == "dead_lettered"

    fixes = await repair(session, user.id)
    await session.commit()
    assert [f["did"] for f in fixes] == ["re-queued it once"]
    await session.refresh(job)
    assert job.status == "pending" and job.payload["_repaired"] is True
    assert quiet_tell == [fixes]

    # Dead again: no second rescue, no repeat of the same news.
    (claimed,) = await queue.claim("w1", limit=1, kinds=["agent.run"])
    await queue.fail(claimed.id, "boom again")
    await session.commit()
    assert await repair(session, user.id) == []
    await session.refresh(job)
    assert job.status == "dead_lettered"


async def test_a_rejected_token_is_flagged_and_reported_once(session, user, quiet_tell):
    account = SourceAccount(
        user_id=user.id, provider="gmail", external_id="me@gmail.com", credentials={},
        last_error="401 Unauthorized: invalid_grant",
    )
    session.add(account)
    await session.commit()
    fixes = await repair(session, user.id)
    await session.commit()
    assert fixes[0]["found"].startswith("gmail rejected the token")
    await session.refresh(account)
    assert account.status == "needs_reconnect"
    assert await repair(session, user.id) == []


async def test_an_expiring_approval_rings_and_a_silent_worker_is_reported(
    session, user, quiet_tell
):
    proposal = await ToolGateway(session).propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.execute(
        text("UPDATE approvals SET expires_at = now() + interval '5 minutes' WHERE id = :id"),
        {"id": proposal.approval.id},
    )
    stale = datetime.now(UTC) - timedelta(minutes=30)
    session.add(WorkerHeartbeat(name="agent", last_tick_at=stale))
    await session.commit()

    fixes = await repair(session, user.id)
    await session.commit()
    founds = [f["found"] for f in fixes]
    assert any("expires in" in f and "message.send" in f for f in founds)
    assert any(f.startswith("agent worker silent for") for f in founds)
    call_jobs = (
        await session.scalars(
            select(Job).where(
                Job.kind == "approval.escalate", Job.payload["stage"].astext == "call"
            )
        )
    ).all()
    assert len(call_jobs) == 1
    applied = select(AuditLog).where(AuditLog.action == "repair.applied")
    rows = (await session.scalars(applied)).all()
    assert len(rows) == len(fixes)
    assert await repair(session, user.id) == []  # same day, same news: silence


# ── standing permissions ──────────────────────────────────────────────
async def test_a_standing_permission_lets_an_r2_run_inside_its_envelope(
    client, auth, session, user
):
    r = await client.post(
        "/v1/permissions",
        json={"tool": "focus.start", "days": 3},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    assert r.json()["tool"] == "focus.start" and r.json()["max_risk"] == "R1"

    # An R2 tool that allows standing permissions, constrained to one argument value.
    r = await client.post(
        "/v1/permissions",
        json={"tool": "mac.capture_screen", "conditions": {"reason": "hud"}, "max_per_day": 1},
        headers=auth,
    )
    assert r.status_code == 201 and r.json()["max_per_day"] == 1
    permission_id = r.json()["id"]

    from jarvis.services.device import DeviceService, generate_keypair, sign

    private_pem, public_pem = generate_keypair()
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(user.id, name="Mac", public_key_pem=public_pem)
    await session.commit()
    mac = await devices.complete_pairing(
        user.id, challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await session.commit()

    gateway = ToolGateway(session)
    inside = await gateway.propose(
        user.id, tool="mac.capture_screen", args={"reason": "hud"}, device_id=mac.id
    )
    await session.commit()
    assert not inside.needs_approval
    assert f"standing:{permission_id}" in inside.policy.reason

    # The per-day rate: the second one inside the envelope waits like any R2...
    second = await gateway.propose(
        user.id, tool="mac.capture_screen", args={"reason": "hud"}, device_id=mac.id
    )
    await session.commit()
    assert second.needs_approval and "standing:" not in second.policy.reason
    # ...and an argument outside it never had the envelope.
    outside = await gateway.propose(
        user.id, tool="mac.capture_screen", args={"reason": "other"}, device_id=mac.id
    )
    assert outside.needs_approval

    listed = (await client.get("/v1/permissions", headers=auth)).json()
    assert {p["tool"] for p in listed} == {"focus.start", "mac.capture_screen"}
    gone = await client.delete(f"/v1/permissions/{permission_id}", headers=auth)
    assert gone.status_code == 204
    again = await client.delete(f"/v1/permissions/{permission_id}", headers=auth)
    assert again.status_code == 404
    remaining = (await client.get("/v1/permissions", headers=auth)).json()
    assert [p["tool"] for p in remaining] == ["focus.start"]


async def test_r3_and_always_ask_tools_cannot_be_pre_approved(client, auth):
    for tool in ("mac.click", "gmail.send", "message.send", "payment.send"):
        r = await client.post("/v1/permissions", json={"tool": tool}, headers=auth)
        assert r.status_code == 409, tool
