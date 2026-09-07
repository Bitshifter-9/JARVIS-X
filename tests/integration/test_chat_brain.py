"""The Chat screen now has a brain behind it.

Before: a persona, one web-search hop and a video button; no memory, no tasks, no policy.
After: memory rides along with the persona, anything actionable is handed to the agent —
which stops at policy like every other path — and what the user says about themselves is
kept, cited, for next time.
"""

from __future__ import annotations

import json

import pytest
from jarvis.db.models.agent import Action, Approval
from jarvis.db.models.ops import Memory
from jarvis.llm.router import LLMRouter
from jarvis.llm.types import CallClass, LLMRequest, LLMResponse
from jarvis.services.identity import IdentityService
from jarvis.services.memory import MemoryService
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


class ChatModel:
    """Persona says ACTION agent for requests; the planner's model proposes one step."""

    name = "chatmodel"
    is_paid = False
    model = "chatmodel"

    def __init__(self, *, persona_reply: str, plan: list[dict], facts: list[str] | None = None):
        self.persona_reply = persona_reply
        self.plan = plan
        self.facts = facts or []
        self.system_prompts: list[str] = []

    def is_configured(self) -> bool:
        return True

    def supports(self, call_class: CallClass) -> bool:
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        system = request.messages[0].content if request.messages else ""
        self.system_prompts.append(system)
        if request.call_class is CallClass.CHAT:
            text = self.persona_reply
        elif request.call_class is CallClass.CLASSIFY and "durable facts" in system:
            text = json.dumps({"facts": self.facts})
        elif request.call_class is CallClass.CLASSIFY:
            text = json.dumps({"intent": "act", "urgency": "normal"})
        else:
            text = json.dumps(
                {
                    "steps": [
                        {"tool": s["tool"], "args_json": json.dumps(s["args"]), "rationale": "t"}
                        for s in self.plan
                    ],
                    "answer": "",
                }
            )
        return LLMResponse(text=text, provider=self.name, model=self.model)

    async def stream(self, request: LLMRequest):  # noqa: ANN201
        # Word by word, like a real provider.
        text = (await self.generate(request)).text
        for i, word in enumerate(text.split(" ")):
            yield (" " if i else "") + word


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("chatbrain@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    response = await client.post(
        "/v1/auth/login", json={"email": "chatbrain@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _install(monkeypatch, model: ChatModel) -> None:
    from jarvis.api.routes import chat as chat_routes

    def factory(session):
        return LLMRouter(
            session,
            providers={model.name: model},
            cascade={cls: (model.name,) for cls in CallClass},
        )

    monkeypatch.setattr(chat_routes, "LLMRouter", factory)


async def test_an_actionable_request_reaches_the_agent_and_stops_at_policy(
    client, auth, session, monkeypatch
):
    model = ChatModel(
        persona_reply='ACTION agent {"request": "email my professor that I am late"}',
        plan=[
            {
                "tool": "gmail.send",
                "args": {"to": "prof@uni.edu", "subject": "Late", "body": "Running late."},
            }
        ],
    )
    _install(monkeypatch, model)

    response = await client.post(
        "/v1/chat", headers=auth, json={"messages": [{"role": "user", "content": "email my prof"}]}
    )
    body = response.json()
    assert response.status_code == 200, body
    assert body["action"]["kind"] == "agent.run"
    assert body["action"]["status"] == "awaiting_approval"
    assert "approval" in body["text"].lower()

    approval = (await session.scalars(select(Approval))).one()
    action = await session.get(Action, approval.action_id)
    assert action.tool == "gmail.send" and action.status == "awaiting_approval"


async def test_what_the_user_says_about_themselves_is_remembered_and_recalled(
    client, auth, session, user, monkeypatch
):
    model = ChatModel(
        persona_reply="Noted.",
        plan=[],
        facts=["The user's thesis advisor is Dr. Rao."],
    )
    _install(monkeypatch, model)

    await client.post(
        "/v1/chat",
        headers=auth,
        json={"messages": [{"role": "user", "content": "my thesis advisor is Dr. Rao by the way"}]},
    )
    kept = (await session.scalars(select(Memory).where(Memory.kind == "episodic"))).all()
    assert [m.content for m in kept] == ["The user's thesis advisor is Dr. Rao."]

    # Next turn: the persona is told what it knows, with a citation.
    await client.post(
        "/v1/chat",
        headers=auth,
        json={"messages": [{"role": "user", "content": "who is my advisor again?"}]},
    )
    persona_system = model.system_prompts[-2]  # the CHAT call of the second turn
    assert "Dr. Rao" in persona_system and "Relevant memory" in persona_system


async def test_a_credential_said_in_chat_is_never_kept(client, auth, session, monkeypatch):
    model = ChatModel(persona_reply="Okay.", plan=[], facts=["The user's password is hunter2"])
    _install(monkeypatch, model)
    await client.post(
        "/v1/chat",
        headers=auth,
        json={"messages": [{"role": "user", "content": "remember my password is hunter2 please"}]},
    )
    assert (await session.scalar(select(Memory))) is None


async def test_recall_is_keyed_on_the_users_words_not_web_results(session, user):
    """The retrieval query is the user's own message; a hostile page cannot steer it."""
    memory = MemoryService(session)
    await memory.remember(
        user.id, content="The user prefers morning study sessions.", kind="semantic"
    )
    await session.commit()
    context = await memory.context_for(user.id, "when should I study?")
    assert "morning study" in context


def _events(raw: str) -> list[dict]:
    return [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]


async def test_the_reply_streams_and_the_stored_turn_matches_it(client, auth, session, monkeypatch):
    from jarvis.db.models.chat import ChatMessage, Conversation

    model = ChatModel(persona_reply="Two things are due today, both by five.", plan=[])
    _install(monkeypatch, model)

    response = await client.post(
        "/v1/chat/stream",
        headers=auth,
        json={"messages": [{"role": "user", "content": "how does today look?"}]},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)

    deltas = "".join(e["text"] for e in events if e["type"] == "delta")
    final = next(e for e in events if e["type"] == "final")
    assert deltas == "Two things are due today, both by five."
    assert final["text"] == deltas, "what was streamed is what was stored"
    assert events[-1]["type"] == "done"
    assert final["conversation_id"]

    saved = (
        await session.scalars(select(ChatMessage).where(ChatMessage.role == "assistant"))
    ).one()
    assert saved.content == deltas and saved.meta["provider"] == "chatmodel"
    conversation = await session.get(Conversation, saved.conversation_id)
    assert conversation.title != "New chat", "titled from the first exchange"


async def test_an_action_line_is_never_streamed_only_its_result(client, auth, monkeypatch):
    model = ChatModel(
        persona_reply='ACTION agent {"request": "list my tasks"}',
        plan=[{"tool": "tasks.list", "args": {}}],
    )
    _install(monkeypatch, model)
    response = await client.post(
        "/v1/chat/stream",
        headers=auth,
        json={"messages": [{"role": "user", "content": "list my tasks"}]},
    )
    events = _events(response.text)
    assert not any(e["type"] == "delta" for e in events), "the machinery stays hidden"
    final = next(e for e in events if e["type"] == "final")
    assert final["action"]["kind"] == "agent.run" and "ACTION" not in final["text"]


async def test_conversations_are_threads_with_their_own_history(client, auth, monkeypatch):
    model = ChatModel(persona_reply="Noted.", plan=[])
    _install(monkeypatch, model)
    first = (await client.post("/v1/conversations", headers=auth)).json()
    second = (await client.post("/v1/conversations", headers=auth)).json()
    for cid, text in ((first["id"], "hello in one"), (second["id"], "hello in two")):
        await client.post(
            "/v1/chat",
            headers=auth,
            json={"conversation_id": cid, "messages": [{"role": "user", "content": text}]},
        )

    one = (await client.get(f"/v1/chat/history?conversation_id={first['id']}", headers=auth)).json()
    two = (
        await client.get(f"/v1/chat/history?conversation_id={second['id']}", headers=auth)
    ).json()
    assert [m["content"] for m in one if m["role"] == "user"] == ["hello in one"]
    assert [m["content"] for m in two if m["role"] == "user"] == ["hello in two"]

    listed = (await client.get("/v1/conversations", headers=auth)).json()
    assert [c["id"] for c in listed][:2] == [second["id"], first["id"]], "newest activity first"

    renamed = await client.patch(
        f"/v1/conversations/{first['id']}", headers=auth, json={"title": "Chat one"}
    )
    assert renamed.json()["title"] == "Chat one"
    gone = await client.delete(f"/v1/conversations/{second['id']}", headers=auth)
    assert gone.json() == {"deleted": True}
    remaining = (await client.get("/v1/conversations", headers=auth)).json()
    assert [c["id"] for c in remaining] == [first["id"]]


async def test_memories_are_listed_and_forgettable(client, auth, session, user):
    await MemoryService(session).remember(
        user.id, content="The user studies at 6am.", kind="semantic"
    )
    await session.commit()
    rows = (await client.get("/v1/memories", headers=auth)).json()
    assert [r["content"] for r in rows] == ["The user studies at 6am."]
    gone = await client.delete(f"/v1/memories/{rows[0]['id']}", headers=auth)
    assert gone.json() == {"forgotten": True}
    assert (await client.get("/v1/memories", headers=auth)).json() == []
    assert await MemoryService(session).context_for(user.id, "when do I study") == ""


async def test_a_run_shows_its_steps(client, auth, monkeypatch):
    model = ChatModel(
        persona_reply='ACTION agent {"request": "list my tasks"}',
        plan=[{"tool": "tasks.list", "args": {}}],
    )
    _install(monkeypatch, model)
    body = (
        await client.post(
            "/v1/chat",
            headers=auth,
            json={"messages": [{"role": "user", "content": "list my tasks"}]},
        )
    ).json()
    run = (await client.get(f"/v1/runs/{body['action']['run_id']}", headers=auth)).json()
    assert run["status"] == "succeeded"
    assert [s["tool"] for s in run["steps"]] == ["tasks.list"]
    assert run["steps"][0]["verdict"] == "verified"
