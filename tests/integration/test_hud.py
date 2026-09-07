"""The HUD (Phase 10.3.1): one call for every tile, and a live stream that carries a
new action within a second of it landing."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.domain import Task
from jarvis.db.models.ops import AuditLog, WorkerHeartbeat
from jarvis.services.identity import IdentityService
from jarvis.services.routines import RoutineService
from jarvis.services.tool_gateway import ToolGateway

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
SEND_ARGS = {"channel": "telegram", "to": "@team", "body": "Running late on the submission."}


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("hud@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    r = await client.post("/v1/auth/login", json={"email": "hud@example.com", "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_hud_counts_real_rows(client, auth, session, user):
    now = datetime.now(UTC)
    session.add(Task(user_id=user.id, title="Due today", due_at=now + timedelta(hours=1)))
    session.add(Task(user_id=user.id, title="Overdue", due_at=now - timedelta(days=2)))
    session.add(WorkerHeartbeat(name="scheduler", last_tick_at=now - timedelta(seconds=5)))
    routines = RoutineService(session)
    await routines.create(user.id, name="Brief", prompt="brief me", cron="0 7 * * *")
    proposal = await ToolGateway(session).propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()
    assert proposal.needs_approval

    r = await client.get("/v1/hud", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    tiles = body["tiles"]
    assert tiles["due_today"] >= 1
    assert tiles["overdue"] == 1
    assert tiles["pending_approvals"] == 1
    assert tiles["actions_today"] == 1
    assert body["workers"]["scheduler"] is not None and body["workers"]["scheduler"] < 60
    assert body["workers"]["agent"] is None
    assert [x["name"] for x in body["routines"]] == ["Brief"]
    assert body["recent"][0]["kind"] == "action"
    assert body["greeting"].startswith("Good ")


async def test_live_stream_carries_a_new_action_within_a_second(client, auth, session, user):
    seen: list[dict] = []

    async def read():
        async with client.stream("GET", "/v1/live?seconds=3", headers=auth) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    seen.append(json.loads(line[6:]))
                    if any(e["kind"] == "action" for e in seen):
                        return

    reader = asyncio.create_task(read())
    await asyncio.sleep(0.3)  # let the stream open and say hello
    await ToolGateway(session).propose(user.id, tool="message.send", args=SEND_ARGS)
    session.add(
        AuditLog(user_id=user.id, actor="system", action="heartbeat.alerted", detail={})
    )
    await session.commit()
    await asyncio.wait_for(reader, timeout=8)

    kinds = [e["kind"] for e in seen]
    assert kinds[0] == "hello"
    assert "action" in kinds
    action = next(e for e in seen if e["kind"] == "action")
    assert action["title"] == "message.send"
