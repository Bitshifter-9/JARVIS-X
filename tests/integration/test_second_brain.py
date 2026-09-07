"""The second brain: profile, teaching, feedback, status, sync-now."""

from __future__ import annotations

import json

import pytest
from jarvis.db.models.ops import Memory, WorkerHeartbeat
from jarvis.llm.router import LLMRouter
from jarvis.llm.types import CallClass, LLMRequest, LLMResponse
from jarvis.services.identity import IdentityService
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


class QuietModel:
    name = "quiet"
    is_paid = False
    model = "quiet"

    def is_configured(self) -> bool:
        return True

    def supports(self, call_class: CallClass) -> bool:
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        system = request.messages[0].content
        if "durable facts" in system:
            text = json.dumps({"facts": []})
        elif "title" in system.lower():
            text = json.dumps({"title": "A chat"})
        elif "style card" in system:
            text = json.dumps({"style": "Short, warm, signs off with 'Cheers'."})
        else:
            text = (
                json.dumps({"intent": "ask", "urgency": "normal"})
                if request.json_schema
                else "Okay."
            )
        return LLMResponse(text=text, provider=self.name, model=self.model)

    async def stream(self, request: LLMRequest):  # noqa: ANN201
        yield "Okay."


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("brain2@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    r = await client.post(
        "/v1/auth/login", json={"email": "brain2@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def quiet(monkeypatch):
    from jarvis.api.routes import chat as chat_routes
    from jarvis.api.routes import profile as profile_routes

    model = QuietModel()

    def factory(session):
        return LLMRouter(
            session,
            providers={model.name: model},
            cascade={cls: (model.name,) for cls in CallClass},
        )

    monkeypatch.setattr(chat_routes, "LLMRouter", factory)
    monkeypatch.setattr(profile_routes, "LLMRouter", factory)
    return model


async def test_the_profile_is_ground_truth_on_every_prompt(client, auth, session, user, quiet):
    from jarvis.services.profile import profile_block

    assert (await client.get("/v1/profile", headers=auth)).json()["rendered"] == ""
    written = (
        await client.put(
            "/v1/profile",
            headers=auth,
            json={
                "about": "Final-year CS student in Hyderabad.",
                "priorities": "Thesis first, hackathon second.",
                "style": "Short answers. No emojis.",
            },
        )
    ).json()
    assert "Thesis first" in written["rendered"]
    block = await profile_block(session, user.id)
    assert "ground truth" in block and "No emojis" in block

    # ...and the chat actually receives it.
    seen: list[str] = []

    async def spy(request):  # noqa: ANN001, ANN202
        seen.append(request.messages[0].content)
        return LLMResponse(text="Okay.", provider="quiet", model="quiet")

    quiet.generate = spy
    await client.post(
        "/v1/chat", headers=auth, json={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert any("Thesis first" in s for s in seen)


async def test_an_explicit_instruction_is_learned_verbatim(client, auth, session, user, quiet):
    await client.post(
        "/v1/chat",
        headers=auth,
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "From now on, call my advisor Dr. Rao, never 'the professor'.",
                }
            ]
        },
    )
    row = (await session.scalars(select(Memory).where(Memory.kind == "instruction"))).one()
    assert row.content.startswith("From now on") and row.importance >= 0.9
    assert row.provenance == {"source": "chat", "taught": True}


async def test_a_thumbs_down_with_a_reason_becomes_a_lesson(client, auth, session, quiet):
    r = await client.post(
        "/v1/chat/feedback",
        headers=auth,
        json={
            "score": -1,
            "note": "too long — I asked for one line",
            "excerpt": "Here are twelve things…",
        },
    )
    assert r.status_code == 201 and r.json()["learned"] is True
    lesson = (await session.scalars(select(Memory).where(Memory.kind == "feedback"))).one()
    assert "too long" in lesson.content and "twelve things" in lesson.content

    bare = await client.post("/v1/chat/feedback", headers=auth, json={"score": 1})
    assert bare.json()["learned"] is False
    stats = (await client.get("/v1/profile/feedback", headers=auth)).json()
    assert (stats["up"], stats["down"]) == (1, 1) and len(stats["lessons"]) == 1


async def test_learning_style_needs_a_mailbox(client, auth, quiet):
    r = await client.post("/v1/profile/learn-style", headers=auth)
    assert r.status_code == 409


async def test_status_reports_real_state_not_flags(client, auth, session, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from jarvis.api.routes import system as system_routes
    from jarvis.core.config import get_settings

    monkeypatch.setattr(system_routes, "_CACHE", {"at": 0.0, "body": None})
    for name in ("telegram_bot_token", "slack_bot_token", "groq_api_key", "fcm_credentials_path"):
        monkeypatch.setattr(get_settings(), name, "")
    session.add(
        WorkerHeartbeat(
            name="agent", last_tick_at=datetime.now(UTC) - timedelta(seconds=5), detail={}
        )
    )
    session.add(
        WorkerHeartbeat(
            name="scheduler", last_tick_at=datetime.now(UTC) - timedelta(hours=2), detail={}
        )
    )
    await session.commit()

    body = (await client.get("/v1/system/status?fresh=true", headers=auth)).json()
    by_name = {c["name"]: c for c in body["checks"]}
    assert by_name["Database"]["ok"] is True
    assert by_name["Worker · agent"]["ok"] is True
    assert by_name["Worker · scheduler"]["ok"] is False, "a stale pulse is a problem, not a flag"
    assert (
        by_name["Worker · connector"]["ok"] is False
        and "never ticked" in by_name["Worker · connector"]["detail"]
    )
    assert by_name["Google"]["ok"] is None and by_name["Google"]["action"] == "connect_google"
    assert by_name["Telegram"]["ok"] is None
    assert body["problems"] >= 2


async def test_sync_now_runs_the_poller_for_one_account(client, auth, session, user, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from jarvis.connectors.base import SyncItem, SyncPage
    from jarvis.db.models.source import SourceAccount
    from jarvis.workers import connector as connector_worker

    account = SourceAccount(
        user_id=user.id,
        provider="gmail",
        external_id="me@gmail.com",
        display_name="me@gmail.com",
        scopes=["gmail.readonly"],
    )
    session.add(account)
    await session.commit()

    class FakeGmail:
        async def sync(self, account_id, cursor):  # noqa: ANN001, ANN202
            return SyncPage(
                items=[
                    SyncItem(
                        provider="gmail",
                        object_id="m1",
                        kind="email",
                        title="Assignment 3",
                        body="due Friday",
                        occurred_at=datetime.now(UTC) + timedelta(days=1),
                    )
                ],
                cursor="h2",
                has_more=False,
            )

    monkeypatch.setattr(connector_worker, "_connectors_for", lambda s, a: [("gmail", FakeGmail())])
    r = await client.post(f"/v1/connectors/{account.id}/sync", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["new"] == {"gmail": 1}
    assert r.json()["result"]["seen"] == {"gmail": 1} and r.json()["error"] is None

    listed = (await client.get("/v1/connectors", headers=auth)).json()
    assert listed[0]["last_sync_result"]["new"] == {"gmail": 1}
    assert listed[0]["last_synced_at"] is not None
