"""Provider webhook ingress.

A webhook acknowledges fast, stores minimal metadata and enqueues (blueprint §3). It
never calls the LLM synchronously — a provider that times out waiting for inference
retries, and the retry storm is worse than the latency.

These routes authenticate by **provider signature, not by user session**, so they must
verify that signature themselves before doing anything.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import JSONResponse

from jarvis.connectors.telegram.client import TelegramClient
from jarvis.connectors.telegram.service import TelegramService
from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.core.security import tokens_equal

log = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> Response:
    """Telegram update ingress.

    Telegram echoes a secret we set on ``setWebhook``; comparing it is what distinguishes
    a real update from anyone who found the URL. Always 200: a non-200 makes Telegram
    retry, and retrying a hostile request helps nobody.
    """
    settings = get_settings()
    expected = settings.telegram_webhook_secret
    if expected and not (secret_token and tokens_equal(secret_token, expected)):
        log.warning("telegram_webhook_bad_secret")
        return Response(status_code=status.HTTP_200_OK)

    try:
        update: dict[str, Any] = await request.json()
    except (ValueError, UnicodeDecodeError):
        log.warning("telegram_webhook_unparseable_body")
        return Response(status_code=status.HTTP_200_OK)

    from jarvis.db.session import session_scope

    try:
        async with session_scope() as session:
            service = TelegramService(session, TelegramClient(settings.telegram_bot_token))
            outcome = await service.handle_update(update)
        log.info("telegram_update", handled=outcome.handled)
    except Exception as exc:  # noqa: BLE001
        # The 200 is the contract, and it holds even when we cannot answer. Anything else
        # makes Telegram redeliver, and a redelivery loop is worse than a dropped update.
        log.error(
            "telegram_webhook_failed", error=str(exc)[:300], error_type=type(exc).__name__
        )

    return Response(status_code=status.HTTP_200_OK)


@router.post("/slack")
async def slack_webhook(
    request: Request,
    timestamp: str | None = Header(default=None, alias="X-Slack-Request-Timestamp"),
    signature: str | None = Header(default=None, alias="X-Slack-Signature"),
) -> Response:
    """Slack Events API ingress (PLAN.md phase 5.1).

    The signature is checked over the **raw body**, before anything parses it — a body
    that has been through ``json.loads`` and back is not the bytes Slack signed. An
    unsigned or stale request gets a 200 and no work: Slack retries a non-2xx for three
    days, and retrying a forged request is worse than dropping it.
    """
    from jarvis.connectors.slack.client import SlackClient, verify_slack_signature
    from jarvis.connectors.slack.service import SlackService

    settings = get_settings()
    raw = await request.body()

    if not verify_slack_signature(
        settings.slack_signing_secret, timestamp=timestamp, body=raw, signature=signature
    ):
        log.warning("slack_webhook_bad_signature")
        return Response(status_code=status.HTTP_200_OK)

    try:
        body: dict[str, Any] = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        log.warning("slack_webhook_unparseable_body")
        return Response(status_code=status.HTTP_200_OK)

    # Slack proves it owns the endpoint by asking us to echo a challenge. Answered only
    # after the signature check, so a stranger cannot use it as an oracle.
    if body.get("type") == "url_verification":
        return JSONResponse({"challenge": body.get("challenge", "")})

    from jarvis.db.session import session_scope

    try:
        async with session_scope() as session:
            service = SlackService(session, SlackClient(settings.slack_bot_token))
            outcome = await service.handle_event(body)
        log.info("slack_event", outcome=outcome)
    except Exception as exc:  # noqa: BLE001
        log.error("slack_webhook_failed", error=str(exc)[:300], error_type=type(exc).__name__)

    return Response(status_code=status.HTTP_200_OK)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request) -> Response:
    """Delivery receipts for template messages (PLAN.md phase 5.4).

    Inbound WhatsApp *conversation* is not a control channel — the escalation ladder
    sends, it does not converse — so this records status and nothing more.
    """
    try:
        body: dict[str, Any] = await request.json()
    except (ValueError, UnicodeDecodeError):
        return Response(status_code=status.HTTP_200_OK)

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for status_row in (change.get("value") or {}).get("statuses", []):
                log.info(
                    "whatsapp_status",
                    message_id=status_row.get("id"),
                    state=status_row.get("status"),
                )
    return Response(status_code=status.HTTP_200_OK)


@router.get("/whatsapp")
async def whatsapp_verify(request: Request) -> Response:
    """Meta's one-time subscription handshake."""
    params = request.query_params
    expected = get_settings().whatsapp_verify_token
    if (
        params.get("hub.mode") == "subscribe"
        and expected
        and tokens_equal(params.get("hub.verify_token", ""), expected)
    ):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=status.HTTP_403_FORBIDDEN)


# ── Twilio voice: approval by call, and the wake-up call (PLAN.md 10.2) ─────────────
def _twilio_url(request: Request) -> str:
    """The URL Twilio signed: our public base plus the path it requested. Behind Caddy
    the socket sees http and an internal host, so the base comes from settings."""
    base = get_settings().base_url.rstrip("/")
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{base}{request.url.path}{query}"


async def _twilio_form(request: Request) -> dict[str, str] | None:
    """The form fields, or ``None`` when the request is not from Twilio."""
    from jarvis.connectors.twilio.client import verify_twilio_signature

    form = {k: str(v) for k, v in (await request.form()).items()}
    token = get_settings().twilio_auth_token
    if not token:
        log.warning("twilio_webhook_unsigned", reason="no auth token configured")
        return None
    if not verify_twilio_signature(
        token, _twilio_url(request), form, request.headers.get("X-Twilio-Signature")
    ):
        log.warning("twilio_webhook_bad_signature")
        return None
    return form


def _xml(body: str, status_code: int = 200) -> Response:
    return Response(content=body, media_type="text/xml", status_code=status_code)


@router.post("/twilio/approval/{approval_id}/{token}")
async def twilio_approval_script(approval_id: str, token: str, request: Request) -> Response:
    """The script for an approval call: read the action aloud, gather yes/no."""
    from jarvis.api.routes.approvals import _summarize
    from jarvis.connectors.twilio.client import twiml_gather, twiml_say
    from jarvis.connectors.twilio.voice import token_ok
    from jarvis.db.models.agent import Action, Approval
    from jarvis.db.session import session_scope

    if not token_ok("approval", approval_id, token) or await _twilio_form(request) is None:
        return _xml(twiml_say("This call is not authorised."), status.HTTP_403_FORBIDDEN)
    async with session_scope() as session:
        approval = await session.get(Approval, _uuid(approval_id))
        action = await session.get(Action, approval.action_id) if approval else None
        if approval is None or action is None:
            return _xml(twiml_say("That approval no longer exists."), status.HTTP_404_NOT_FOUND)
        if approval.decision is not None:
            return _xml(twiml_say(f"That was already {approval.decision}. Goodbye."))
        summary = _summarize(action.tool, action.args or {})
    return _xml(
        twiml_gather(
            f"Jarvis here. I need your approval for: {summary}. "
            "Say yes or press 1 to approve. Say no or press 2 to reject.",
            action_url=f"{_twilio_url(request)}/decide",
            fallback="I did not catch that. It stays waiting in the app. Goodbye.",
            hints="yes, no, approve, reject",
        )
    )


@router.post("/twilio/approval/{approval_id}/{token}/decide")
async def twilio_approval_decide(approval_id: str, token: str, request: Request) -> Response:
    """Twilio posts what was said or pressed; a valid answer decides the approval."""
    from jarvis.connectors.twilio.client import twiml_gather, twiml_say
    from jarvis.connectors.twilio.voice import interpret, token_ok
    from jarvis.core.errors import Conflict, NotFound
    from jarvis.db.models.agent import Approval
    from jarvis.db.session import session_scope
    from jarvis.services.tool_gateway import ToolGateway

    form = await _twilio_form(request)
    if not token_ok("approval", approval_id, token) or form is None:
        return _xml(twiml_say("This call is not authorised."), status.HTTP_403_FORBIDDEN)
    answer = interpret(form.get("Digits"), form.get("SpeechResult"))
    if answer is None:
        script_url = _twilio_url(request).removesuffix("/decide")
        return _xml(
            twiml_gather(
                "Sorry, was that a yes or a no? Press 1 to approve or 2 to reject.",
                action_url=script_url + "/decide",
                fallback="No answer. It stays waiting in the app. Goodbye.",
                hints="yes, no",
            )
        )
    async with session_scope() as session:
        approval = await session.get(Approval, _uuid(approval_id))
        if approval is None:
            return _xml(twiml_say("That approval no longer exists."), status.HTTP_404_NOT_FOUND)
        try:
            await ToolGateway(session).decide(
                approval.user_id, approval.id, approved=answer, decided_by="phone"
            )
        except (Conflict, NotFound) as exc:
            return _xml(twiml_say(f"{exc.detail or exc.title}. Goodbye."))
    log.info("approval_decided_by_phone", approval_id=approval_id, approved=answer)
    return _xml(twiml_say("Approved. Running it now. Goodbye." if answer else "Rejected. Goodbye."))


@router.post("/twilio/routine/{routine_id}/{token}")
async def twilio_routine_script(routine_id: str, token: str, request: Request) -> Response:
    """The wake-up call: speak the routine's latest result; 1 snoozes ten minutes."""
    from jarvis.connectors.twilio.client import twiml_gather, twiml_say
    from jarvis.connectors.twilio.voice import token_ok
    from jarvis.db.models.ops import Routine
    from jarvis.db.session import session_scope

    if not token_ok("routine", routine_id, token) or await _twilio_form(request) is None:
        return _xml(twiml_say("This call is not authorised."), status.HTTP_403_FORBIDDEN)
    async with session_scope() as session:
        routine = await session.get(Routine, _uuid(routine_id))
        if routine is None:
            return _xml(twiml_say("That routine no longer exists."), status.HTTP_404_NOT_FOUND)
        name, text = routine.name, routine.last_result or "Nothing to report."
    return _xml(
        twiml_gather(
            f"Good morning. This is Jarvis with your {name}. {text} "
            "Press 1 to hear it again in ten minutes, or hang up.",
            action_url=f"{_twilio_url(request)}/snooze",
            fallback="Goodbye.",
            hints="snooze, again",
        )
    )


@router.post("/twilio/routine/{routine_id}/{token}/snooze")
async def twilio_routine_snooze(routine_id: str, token: str, request: Request) -> Response:
    from jarvis.connectors.twilio.client import twiml_say
    from jarvis.connectors.twilio.voice import token_ok
    from jarvis.db.models.ops import Routine
    from jarvis.db.session import session_scope
    from jarvis.services.routines import RoutineService

    form = await _twilio_form(request)
    if not token_ok("routine", routine_id, token) or form is None:
        return _xml(twiml_say("This call is not authorised."), status.HTTP_403_FORBIDDEN)
    wants_snooze = form.get("Digits") == "1" or "snooze" in (form.get("SpeechResult") or "").lower()
    if not wants_snooze:
        return _xml(twiml_say("Goodbye."))
    async with session_scope() as session:
        routine = await session.get(Routine, _uuid(routine_id))
        if routine is None:
            return _xml(twiml_say("That routine no longer exists."), status.HTTP_404_NOT_FOUND)
        await RoutineService(session).fire(routine, delay_seconds=600)
    return _xml(twiml_say("Snoozed. I will call again in ten minutes. Goodbye."))


def _uuid(value: str):  # noqa: ANN202
    import uuid

    try:
        return uuid.UUID(value)
    except ValueError:
        return uuid.UUID(int=0)


# ── call Jarvis (PLAN.md 10.2.3): an inbound Twilio call from the owner's own number ──
_INBOUND_TURNS = 8


async def _owner_for_number(session, number: str):  # noqa: ANN001, ANN202
    from sqlalchemy import select

    from jarvis.db.models.ops import NotificationEndpoint

    digits = "".join(ch for ch in number if ch.isdigit())[-10:]
    if not digits:
        return None
    rows = (
        await session.scalars(
            select(NotificationEndpoint).where(
                NotificationEndpoint.channel == "call", NotificationEndpoint.enabled.is_(True)
            )
        )
    ).all()
    for row in rows:
        if "".join(ch for ch in row.address if ch.isdigit())[-10:] == digits:
            return row.user_id
    return None


@router.post("/twilio/inbound")
async def twilio_inbound(request: Request) -> Response:
    """Someone rang the Jarvis number. Only the owner's own number gets past hello."""
    from jarvis.connectors.twilio.client import twiml_gather, twiml_say
    from jarvis.db.session import session_scope

    form = await _twilio_form(request)
    if form is None:
        return _xml(twiml_say("This call is not authorised."), status.HTTP_403_FORBIDDEN)
    async with session_scope() as session:
        user_id = await _owner_for_number(session, form.get("From", ""))
    if user_id is None:
        log.info("twilio_inbound_unknown_caller")
        return _xml(twiml_say("Sorry, this line only answers its owner. Goodbye."))
    return _xml(
        twiml_gather(
            "Jarvis here. What do you need?",
            action_url=f"{_twilio_url(request)}/turn?n=1",
            fallback="I did not catch anything. Goodbye.",
            hints="what is due, brief me, remind me, what did I miss",
        )
    )


@router.post("/twilio/inbound/turn")
async def twilio_inbound_turn(request: Request, n: int = 1) -> Response:
    """One spoken turn: what they said → the chat model → spoken back, then listen again."""
    from jarvis.connectors.twilio.client import twiml_gather, twiml_say
    from jarvis.db.session import session_scope

    form = await _twilio_form(request)
    if form is None:
        return _xml(twiml_say("This call is not authorised."), status.HTTP_403_FORBIDDEN)
    said = (form.get("SpeechResult") or "").strip()
    async with session_scope() as session:
        user_id = await _owner_for_number(session, form.get("From", ""))
        if user_id is None:
            return _xml(twiml_say("Sorry, this line only answers its owner. Goodbye."))
        if not said:
            return _xml(twiml_say("I did not catch that. Goodbye."))
        if said.lower().rstrip(".!") in ("bye", "goodbye", "that's all", "thanks bye", "stop"):
            return _xml(twiml_say("Goodbye."))
        reply = await _answer_by_voice(session, user_id, said, form.get("CallSid", ""))
    if n >= _INBOUND_TURNS:
        return _xml(twiml_say(f"{reply} That is all I can do on this call. Goodbye."))
    base = _twilio_url(request).split("?", 1)[0]
    return _xml(
        twiml_gather(
            f"{reply} Anything else?",
            action_url=f"{base}?n={n + 1}",
            fallback="Goodbye.",
            hints="yes, no, goodbye",
        )
    )


async def _answer_by_voice(session, user_id, said: str, call_sid: str) -> str:  # noqa: ANN001
    """A spoken answer: the chat persona and profile, two sentences, no markdown. Every
    turn is written to a "Phone calls" thread so the app shows the conversation."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from jarvis.api.routes.chat import _persona
    from jarvis.core.ids import uuid7
    from jarvis.db.models.chat import ChatMessage, Conversation
    from jarvis.llm.router import LLMRouter
    from jarvis.llm.types import Message
    from jarvis.services.profile import profile_block

    system = (
        _persona()
        + "\n\nYou are on a PHONE CALL. Answer in one or two short spoken sentences, no "
        "markdown, no lists, no ACTION lines — if something needs doing, say you will "
        "queue it and that it will wait in Approvals."
        + "\n\n"
        + await profile_block(session, user_id)
    )
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.title == "Phone calls",
            Conversation.archived_at.is_(None),
        )
    )
    if conversation is None:
        conversation = Conversation(id=uuid7(), user_id=user_id, title="Phone calls")
        session.add(conversation)
        await session.flush()
    history = (
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.id.desc())
            .limit(6)
        )
    ).all()
    messages = [Message("system", system)]
    messages += [Message(m.role, m.content) for m in reversed(history)]
    messages.append(Message("user", said[:2000]))
    try:
        response = await LLMRouter(session).chat(messages, user_id=user_id, max_tokens=220)
        reply = response.text.strip().replace("*", "").replace("#", "") or "I have nothing on that."
    except Exception as exc:  # noqa: BLE001
        log.warning("inbound_call_model_failed", error=str(exc)[:200])
        reply = "I cannot think right now; try me again in a minute."
    now = datetime.now(UTC)
    for role, content in (("user", said), ("assistant", reply)):
        session.add(
            ChatMessage(
                id=uuid7(),
                user_id=user_id,
                conversation_id=conversation.id,
                role=role,
                content=content,
                meta={"channel": "call", "call_sid": call_sid},
            )
        )
    conversation.last_message_at = now
    await session.flush()
    return reply[:1200]
