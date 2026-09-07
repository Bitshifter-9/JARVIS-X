"""Call Jarvis (PLAN.md 10.2.3): only the owner's number gets an answer; each turn is
answered by the chat model and kept in a thread; a stranger is turned away."""

from __future__ import annotations

import pytest
from jarvis.connectors.twilio.client import twilio_signature
from jarvis.core.config import get_settings
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.db.models.ops import NotificationEndpoint
from jarvis.llm.types import CallClass, LLMRequest, LLMResponse
from jarvis.services.identity import IdentityService
from sqlalchemy import select

AUTH_TOKEN = "twilio-test-auth-token"  # noqa: S105


class Answering:
    name = "answering"
    is_paid = False
    model = "answering"
    systems: list[str] = []

    def is_configured(self) -> bool:
        return True

    def supports(self, call_class: CallClass) -> bool:
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.systems.append(request.messages[0].content)
        return LLMResponse(text="Two things are due today.", provider=self.name, model=self.model)


@pytest.fixture
async def owner(session):
    u = await IdentityService(session).register("caller@example.com", "correct-horse-battery")
    session.add(NotificationEndpoint(user_id=u.id, channel="call", address="+91 99999 99999"))
    await session.commit()
    return u


@pytest.fixture
def twilio(monkeypatch):
    from jarvis.api.routes import webhooks

    s = get_settings()
    before = s.twilio_auth_token
    s.twilio_auth_token = AUTH_TOKEN
    model = Answering()
    Answering.systems = []
    _ = webhooks  # the router is swapped per call in quiet_router
    yield model
    s.twilio_auth_token = before


def _signed(url: str, form: dict[str, str]) -> dict[str, str]:
    return {"X-Twilio-Signature": twilio_signature(AUTH_TOKEN, url, form)}


def _url(path: str) -> str:
    return get_settings().base_url.rstrip("/") + path


@pytest.fixture
def quiet_router(monkeypatch, twilio):
    import jarvis.api.routes.webhooks as webhooks

    real = webhooks._answer_by_voice

    async def patched(session, user_id, said, call_sid):  # noqa: ANN001
        from jarvis.llm import router as router_module

        original = router_module.LLMRouter

        class Fake(original):  # type: ignore[misc]
            def __init__(self, session, *a, **k):  # noqa: ANN001
                super().__init__(
                    session,
                    providers={twilio.name: twilio},
                    cascade={c: (twilio.name,) for c in CallClass},
                )

        monkeypatch.setattr(router_module, "LLMRouter", Fake)
        try:
            return await real(session, user_id, said, call_sid)
        finally:
            monkeypatch.setattr(router_module, "LLMRouter", original)

    monkeypatch.setattr(webhooks, "_answer_by_voice", patched)
    return twilio


async def test_the_owner_is_greeted_and_answered(client, session, owner, quiet_router):
    url = _url("/webhooks/twilio/inbound")
    form = {"CallSid": "CA9", "From": "+919999999999"}
    r = await client.post("/webhooks/twilio/inbound", data=form, headers=_signed(url, form))
    assert r.status_code == 200 and "<Gather" in r.text and "What do you need" in r.text

    turn_url = _url("/webhooks/twilio/inbound/turn?n=1")
    said = {"CallSid": "CA9", "From": "+919999999999", "SpeechResult": "what is due today"}
    r = await client.post(
        "/webhooks/twilio/inbound/turn?n=1", data=said, headers=_signed(turn_url, said)
    )
    assert r.status_code == 200, r.text
    assert "Two things are due today." in r.text and "Anything else" in r.text
    assert "?n=2" in r.text
    assert "PHONE CALL" in quiet_router.systems[0]

    thread = await session.scalar(select(Conversation).where(Conversation.title == "Phone calls"))
    rows = (
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == thread.id)
            .order_by(ChatMessage.id)
        )
    ).all()
    by_role = {m.role: m for m in rows}
    assert set(by_role) == {"user", "assistant"} and len(rows) == 2
    assert by_role["user"].content == "what is due today"
    assert by_role["assistant"].content == "Two things are due today."
    assert by_role["user"].meta["call_sid"] == "CA9"


async def test_a_stranger_gets_no_answer(client, session, owner, quiet_router):
    url = _url("/webhooks/twilio/inbound")
    form = {"CallSid": "CA1", "From": "+15550001111"}
    r = await client.post("/webhooks/twilio/inbound", data=form, headers=_signed(url, form))
    assert "only answers its owner" in r.text and "<Gather" not in r.text
    assert quiet_router.systems == []


async def test_an_unsigned_inbound_call_is_refused(client, owner, quiet_router):
    r = await client.post("/webhooks/twilio/inbound", data={"From": "+919999999999"})
    assert r.status_code == 403


async def test_goodbye_ends_the_call_and_the_turn_cap_holds(client, session, owner, quiet_router):
    turn_url = _url("/webhooks/twilio/inbound/turn?n=3")
    bye = {"CallSid": "CA9", "From": "+919999999999", "SpeechResult": "Goodbye"}
    r = await client.post(
        "/webhooks/twilio/inbound/turn?n=3", data=bye, headers=_signed(turn_url, bye)
    )
    assert "Goodbye." in r.text and "<Gather" not in r.text

    last_url = _url("/webhooks/twilio/inbound/turn?n=8")
    more = {"CallSid": "CA9", "From": "+919999999999", "SpeechResult": "and tomorrow?"}
    r = await client.post(
        "/webhooks/twilio/inbound/turn?n=8", data=more, headers=_signed(last_url, more)
    )
    assert "That is all I can do on this call" in r.text and "<Gather" not in r.text
