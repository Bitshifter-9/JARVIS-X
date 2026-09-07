"""Personas, the 3-minute interview, and documents on demand (PLAN.md 10.3.2–10.3.4)."""

from __future__ import annotations

import json

import pytest
from jarvis.core.config import get_settings
from jarvis.db.models.chat import Conversation
from jarvis.db.models.ops import Artifact, Memory
from jarvis.llm.router import LLMRouter
from jarvis.llm.types import CallClass, LLMRequest, LLMResponse
from jarvis.services.agent.executor import dispatch_action
from jarvis.services.identity import IdentityService
from jarvis.services.tool_gateway import ToolGateway
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


class SeeingModel:
    """Answers blandly, remembers every system prompt it was given."""

    name = "seeing"
    is_paid = False
    model = "seeing"

    def __init__(self) -> None:
        self.systems: list[str] = []
        self.fail_interview = False

    def is_configured(self) -> bool:
        return True

    def supports(self, call_class: CallClass) -> bool:
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        system = request.messages[0].content
        self.systems.append(system)
        if "interview" in system:
            if self.fail_interview:
                raise RuntimeError("model down")
            text = json.dumps(
                {
                    "about": "You are Pranav, building JARVIS X.",
                    "priorities": "You are shipping Phase 10 this month.",
                    "people": "Amma is your mother; Prof. Rao teaches CS401.",
                    "style": "You want short, direct replies.",
                    "decisions": "You decide fast.\n\nHard rules:\nNever email Prof. Rao unasked.",
                }
            )
        elif "durable facts" in system:
            text = json.dumps({"facts": []})
        elif "title" in system.lower():
            text = json.dumps({"title": "A chat"})
        else:
            text = json.dumps({"intent": "ask"}) if request.json_schema else "Okay."
        return LLMResponse(text=text, provider=self.name, model=self.model)

    async def stream(self, request: LLMRequest):  # noqa: ANN201
        self.systems.append(request.messages[0].content)
        yield "Okay."


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("persona@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    r = await client.post(
        "/v1/auth/login", json={"email": "persona@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def seeing(monkeypatch):
    from jarvis.api.routes import chat as chat_routes
    from jarvis.api.routes import profile as profile_routes

    model = SeeingModel()

    def factory(session):
        return LLMRouter(
            session,
            providers={model.name: model},
            cascade={cls: (model.name,) for cls in CallClass},
        )

    monkeypatch.setattr(chat_routes, "LLMRouter", factory)
    monkeypatch.setattr(profile_routes, "LLMRouter", factory)
    return model


@pytest.fixture
def artifact_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "artifact_dir", str(tmp_path))
    return tmp_path


# ── personas ──────────────────────────────────────────────────────────
async def test_presets_are_listed_and_custom_ones_are_owned(client, auth):
    r = await client.get("/v1/personas", headers=auth)
    assert r.status_code == 200
    assert {p["key"] for p in r.json()} >= {"jarvis", "coach", "analyst", "study"}

    r = await client.put(
        "/v1/personas/My Tutor",
        json={"name": "My tutor", "instructions": "Explain like I am five."},
        headers=auth,
    )
    assert r.status_code == 200 and r.json()["key"] == "my-tutor"
    keys = {p["key"] for p in (await client.get("/v1/personas", headers=auth)).json()}
    assert "my-tutor" in keys

    r = await client.put(
        "/v1/personas/coach", json={"name": "x", "instructions": "y"}, headers=auth
    )
    assert r.status_code == 409  # presets are not editable
    assert (await client.delete("/v1/personas/my-tutor", headers=auth)).status_code == 204
    assert (await client.delete("/v1/personas/my-tutor", headers=auth)).status_code == 404


async def test_a_persona_changes_the_prompt_and_the_thread_remembers_it(
    client, auth, session, seeing
):
    r = await client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "persona": "coach"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert "demanding but kind coach" in seeing.systems[0]
    conversation_id = r.json()["conversation_id"]

    # No persona on the next turn: the thread's persona still applies.
    seeing.systems.clear()
    r = await client.post(
        "/v1/chat",
        json={
            "messages": [{"role": "user", "content": "again"}],
            "conversation_id": conversation_id,
        },
        headers=auth,
    )
    assert r.status_code == 200
    assert "demanding but kind coach" in seeing.systems[0]
    conversation = await session.get(Conversation, conversation_id)
    assert conversation.persona == "coach"
    listed = (await client.get("/v1/conversations", headers=auth)).json()
    assert listed[0]["persona"] == "coach"

    # The default persona adds nothing.
    seeing.systems.clear()
    r = await client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "plain"}], "persona": "jarvis"},
        headers=auth,
    )
    assert "adopt this persona" not in seeing.systems[0]


# ── the interview ─────────────────────────────────────────────────────
ANSWERS = {
    "about": "I'm Pranav, I build JARVIS X.",
    "priorities": "Ship phase 10.",
    "people": "Amma is my mother. Prof. Rao teaches CS401.",
    "style": "Short and direct.",
    "decisions": "I decide fast.",
    "rules": "Never email Prof. Rao unasked.",
}


async def test_the_interview_fills_every_section(client, auth, session, user, seeing):
    questions = (await client.get("/v1/profile/interview", headers=auth)).json()
    assert [q["key"] for q in questions] == list(ANSWERS)

    r = await client.post("/v1/profile/interview", json={"answers": ANSWERS}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["distilled"] is True
    for key in ("about", "priorities", "people", "style", "decisions"):
        assert body[key].strip(), key
    assert "Hard rules" in body["decisions"]
    assert "Amma" in body["rendered"]

    rule = await session.scalar(select(Memory).where(Memory.kind == "instruction"))
    assert rule is not None and "Prof. Rao" in rule.content

    # The next chat cites the profile.
    seeing.systems.clear()
    await client.post(
        "/v1/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=auth
    )
    assert "Amma is your mother" in seeing.systems[0]


async def test_the_interview_keeps_the_answers_when_the_model_is_down(client, auth, seeing):
    seeing.fail_interview = True
    r = await client.post("/v1/profile/interview", json={"answers": ANSWERS}, headers=auth)
    assert r.status_code == 200
    assert r.json()["distilled"] is False
    assert r.json()["about"] == ANSWERS["about"]
    assert "Never email Prof. Rao" in r.json()["decisions"]


async def test_an_empty_interview_is_refused(client, auth):
    r = await client.post("/v1/profile/interview", json={"answers": {}}, headers=auth)
    assert r.status_code == 409


# ── documents on demand ───────────────────────────────────────────────
async def test_docs_create_produces_bytes_that_verify(client, auth, session, user, artifact_dir):
    proposal = await ToolGateway(session).propose(
        user.id,
        tool="docs.create",
        args={"title": "My week", "content": "# Week\n\n- Two things due", "format": "md"},
    )
    await session.commit()
    assert not proposal.needs_approval  # R1
    action_id = proposal.action.id

    outcome = await dispatch_action(session, action_id)
    await session.commit()
    assert outcome["verdict"] == "verified", outcome

    artifact = await session.scalar(select(Artifact).where(Artifact.action_id == action_id))
    assert artifact.kind == "document" and artifact.filename == "My week.md"
    stored = (artifact_dir / str(user.id) / f"{artifact.id}.md").read_bytes()
    assert stored == b"# Week\n\n- Two things due"

    r = await client.get(f"/v1/artifacts/{artifact.id}", headers=auth)
    assert r.status_code == 200 and r.content == b"# Week\n\n- Two things due"
    assert r.headers["content-type"].startswith("text/markdown")


async def test_docs_create_renders_a_real_pdf(session, user, artifact_dir):
    proposal = await ToolGateway(session).propose(
        user.id,
        tool="docs.create",
        args={"title": "Report", "content": "# Report\n\nHello **world**.", "format": "pdf"},
    )
    await session.commit()
    action_id = proposal.action.id
    outcome = await dispatch_action(session, action_id)
    await session.commit()
    assert outcome["verdict"] == "verified", outcome
    artifact = await session.scalar(select(Artifact).where(Artifact.action_id == action_id))
    assert artifact.content_type == "application/pdf"
    assert (artifact_dir / str(user.id) / f"{artifact.id}.pdf").read_bytes()[:5] == b"%PDF-"


async def test_a_bad_format_fails_closed(session, user, artifact_dir):
    proposal = await ToolGateway(session).propose(
        user.id, tool="docs.create", args={"title": "x", "content": "y", "format": "exe"}
    )
    await session.commit()
    outcome = await dispatch_action(session, proposal.action.id)
    assert outcome["verdict"] == "failed"
    assert await session.scalar(select(Artifact)) is None
