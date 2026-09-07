"""Talk to Jarvis: the conversational surface behind the app's Chat screen.

Threads live in ``conversations``; every turn is stored with the account so the Mac and
the phone show one history. Two ways to ask: ``POST /v1/chat`` answers in one piece,
``POST /v1/chat/stream`` sends the reply as it is written (server-sent events) and then
the same final record — so both paths run one code path for search, the agent, video and
memory, and the streamed text is never different from the stored one.

Anything with an effect on the outside world stays with the agent core and its policy
gate: the persona only ever hands such requests over, it never performs them.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings
from jarvis.core.errors import NotFound
from jarvis.core.ids import uuid7
from jarvis.core.logging import get_logger
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.db.queue import JobQueue
from jarvis.llm.router import LLMRouter
from jarvis.llm.types import CallClass, LLMRequest, Message
from jarvis.services.agent.runtime import describe
from jarvis.services.profile import profile_block

log = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["chat"])

PERSONA_TEMPLATE = (
    "You are JARVIS X, a sharp, capable personal operations assistant. "
    "Today is {today}. Be concise, warm and direct. Use Markdown when it helps: short "
    "headings, bullet lists, **bold** for the key point, fenced code blocks for code or "
    "commands. Answer substantively; never deflect with 'I am just an assistant'.\n"
    "Your built-in knowledge has a cutoff and is stale. For ANY question about news, "
    "current events, recent releases, prices, weather, sports, or anything where "
    "freshness matters, do NOT answer from memory — reply with ONLY this line:\n"
    'ACTION search {{"query": "<web search query>"}}\n'
    "You will receive live results to answer from. Never invent headlines or dates.\n"
    "If the user asks you to DO something — add, change or acknowledge a task, start a "
    "focus session, send a message or email, post to Slack, open or check something on "
    "their Mac or phone, take a screenshot, look something up in their own notes or "
    "memory, or asks what is due or at risk — do not answer yourself. Reply with ONLY "
    'this line:\nACTION agent {{"request": "<their request, in their words>"}}\n'
    "The agent runs it under policy and you will relay its result.\n"
    "If the user asks you to create, render or make a video, include on its own "
    'final line exactly: ACTION youtube.generate {{"topic": "<topic>"}}. The topic '
    "MUST keep every requested detail verbatim.\n"
    "If they ask you to research something across websites, compare products or prices, "
    "or find something on a specific site, that is an ACTION agent request too (the agent "
    "browses on its own, with a step budget, and never submits a form without approval).\n"
    "If they ask you to write, create or make a document, report, PDF, file, sheet or "
    "CSV from content, that is also an ACTION agent request (the agent creates and "
    "delivers the file).\n"
    "The app's tabs are exactly: Jarvis (this chat), Home, Routines, Goals, Approvals, "
    "Timeline, Connections, Train, Videos, Devices, Settings. Never invent tabs or "
    "features that are not listed."
    "{extra}{persona}"
)

_GENERATE = re.compile(r"^ACTION youtube\.generate ({.*})\s*$", re.M)
_SEARCH = re.compile(r"^\s*ACTION search ({.*})\s*$", re.M)
_AGENT = re.compile(r"^\s*ACTION agent ({.*})\s*$", re.M)

MEMO_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"facts": {"type": "array", "items": {"type": "string"}}},
    "required": ["facts"],
}
TITLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
}
PROVIDERS = (
    "auto", "gateway", "groq", "cerebras", "gemini", "openrouter_free",
    "openrouter_free2", "openrouter_paid", "ollama",
)


def _persona(fragment: str = "") -> str:
    s = get_settings()
    now = datetime.now(ZoneInfo(s.timezone))
    extra = (
        f"\n\nThe user's standing instructions:\n{s.persona_extra.strip()}"
        if s.persona_extra.strip()
        else ""
    )
    persona = (
        f"\n\nFor this conversation, adopt this persona:\n{fragment.strip()}"
        if fragment.strip()
        else ""
    )
    return PERSONA_TEMPLATE.format(
        today=now.strftime("%A, %d %B %Y, %H:%M %Z"), extra=extra, persona=persona
    )


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1, max_length=30)
    conversation_id: str | None = None
    provider: str | None = Field(default=None, max_length=24)
    persona: str | None = Field(default=None, max_length=64)


# ── conversations ──────────────────────────────────────────────────────
def _conversation_out(c: Conversation) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "title": c.title,
        "created_at": c.created_at.isoformat(),
        "last_message_at": (c.last_message_at or c.created_at).isoformat(),
        "archived": c.archived_at is not None,
        "pinned": c.pinned_at is not None,
        "persona": c.persona,
    }


@router.get("/conversations")
async def list_conversations(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(Conversation)
            .where(Conversation.user_id == user.id, Conversation.archived_at.is_(None))
            .order_by(
                Conversation.pinned_at.desc().nulls_last(),
                Conversation.last_message_at.desc().nulls_last(),
                Conversation.id.desc(),
            )
            .limit(100)
        )
    ).all()
    return [_conversation_out(c) for c in rows]


@router.post("/conversations", status_code=201)
async def create_conversation(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    conversation = Conversation(id=uuid7(), user_id=user.id, title="New chat")
    session.add(conversation)
    await session.flush()
    return _conversation_out(conversation)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None
    pinned: bool | None = None


@router.patch("/conversations/{conversation_id}")
async def patch_conversation(
    conversation_id: uuid.UUID, body: ConversationPatch, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    conversation = await _owned_conversation(session, user.id, conversation_id)
    if body.title is not None:
        conversation.title = body.title.strip()
    if body.archived is not None:
        conversation.archived_at = datetime.now(UTC) if body.archived else None
    if body.pinned is not None:
        conversation.pinned_at = datetime.now(UTC) if body.pinned else None
    await session.flush()
    return _conversation_out(conversation)


@router.post("/conversations/{conversation_id}/export")
async def export_conversation(
    conversation_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    fmt: str = "md",
) -> dict[str, Any]:
    """A whole conversation as a document (FEATURES-50 #18). Returns the Markdown and a
    stored artifact you can open again from Documents."""
    from jarvis.db.models.chat import ChatMessage
    from jarvis.services.documents import render_document, store_artifact

    conversation = await _owned_conversation(session, user.id, conversation_id)
    messages = (
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        )
    ).all()
    title = conversation.title or "Conversation"
    lines = [f"# {title}", ""]
    for m in messages:
        who = "You" if m.role == "user" else "Jarvis"
        when = m.created_at.strftime("%d %b %H:%M") if m.created_at else ""
        lines.append(f"**{who}**{f' · {when}' if when else ''}")
        lines.append("")
        lines.append(m.content)
        lines.append("")
    markdown = "\n".join(lines)

    data, content_type, filename = await render_document(title, markdown, fmt)
    artifact = await store_artifact(
        session, user_id=user.id, kind="document", filename=filename,
        content_type=content_type, data=data,
    )
    await session.flush()
    return {
        "artifact": {"id": str(artifact.id), "url": f"/v1/artifacts/{artifact.id}",
                     "filename": artifact.filename},
        "markdown": markdown,
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    conversation = await _owned_conversation(session, user.id, conversation_id)
    await session.delete(conversation)
    await session.flush()
    return {"deleted": True}


async def _owned_conversation(
    session, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:  # noqa: ANN001
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise NotFound("Conversation")
    return conversation


async def _resolve_conversation(session, user_id: uuid.UUID, wanted: str | None) -> Conversation:  # noqa: ANN001
    """The thread a message belongs to: the one named, else the newest, else a new one."""
    if wanted:
        return await _owned_conversation(session, user_id, uuid.UUID(wanted))
    latest = await session.scalar(
        select(Conversation)
        .where(Conversation.user_id == user_id, Conversation.archived_at.is_(None))
        .order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.id.desc())
        .limit(1)
    )
    if latest is not None:
        return latest
    conversation = Conversation(id=uuid7(), user_id=user_id, title="New chat")
    session.add(conversation)
    await session.flush()
    return conversation


# ── history ────────────────────────────────────────────────────────────
async def _persona_for(session, user_id, body: ChatRequest, conversation: Conversation) -> str:  # noqa: ANN001
    """The request's persona wins and is remembered on the thread; otherwise the thread's."""
    from jarvis.services.personas import resolve

    if body.persona is not None:
        conversation.persona = body.persona or None
    return await resolve(session, user_id, conversation.persona)


@router.get("/chat/history")
async def history(
    user: CurrentUser, session: SessionDep, conversation_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """The conversation, oldest first — shared by every device on the account."""
    query = select(ChatMessage).where(ChatMessage.user_id == user.id)
    if conversation_id:
        query = query.where(ChatMessage.conversation_id == uuid.UUID(conversation_id))
    else:
        latest = await session.scalar(
            select(Conversation)
            .where(Conversation.user_id == user.id, Conversation.archived_at.is_(None))
            .order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.id.desc())
            .limit(1)
        )
        if latest is not None:
            query = query.where(ChatMessage.conversation_id == latest.id)
    rows = (
        await session.scalars(query.order_by(ChatMessage.id.desc()).limit(min(limit, 200)))
    ).all()
    return [
        {
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "at": m.created_at.isoformat(),
            "conversation_id": str(m.conversation_id) if m.conversation_id else None,
            "meta": m.meta or {},
        }
        for m in reversed(rows)
    ]


# ── the turn ───────────────────────────────────────────────────────────
def _messages_for(
    body: ChatRequest, recalled: str, profile: str = "", persona: str = ""
) -> list[Message]:
    system = _persona(persona)
    if profile:
        system += f"\n\n{profile}"
    if recalled:
        system += f"\n\n{recalled}"
    return [Message("system", system)] + [Message(t.role, t.content) for t in body.messages]


def _prefer(body: ChatRequest) -> str | None:
    wanted = (body.provider or get_settings().chat_provider or "auto").strip()
    return None if wanted in ("", "auto") or wanted not in PROVIDERS else wanted


async def _finish(
    session,  # noqa: ANN001
    user,  # noqa: ANN001
    body: ChatRequest,
    llm: LLMRouter,
    messages: list[Message],
    text: str,
    provider: str,
    conversation: Conversation,
) -> dict[str, Any]:
    """Everything that happens after the model's first answer: one code path for both
    the streamed and the whole-reply routes."""
    from jarvis.services.memory import MemoryService

    memory = MemoryService(session)
    latest = body.messages[-1].content
    searched = None
    action = None

    # ── live search: one hop, results injected as untrusted data ───────
    if match := _SEARCH.search(text):
        from jarvis.services.event.service import EventService
        from jarvis.services.youtube.pipeline import research

        try:
            searched = str(json.loads(match.group(1)).get("query", "")).strip()
        except json.JSONDecodeError:
            searched = ""
        results = await research(searched, max_results=6) if searched else []
        grounding = (
            f"Live web search results for '{searched}' (fetched just now):\n"
            + EventService.untrusted("\n".join(results))
            if results
            else "The web search returned no results — say you could not fetch "
            "current information, and answer only what you know for certain."
        )
        try:
            response = await llm.chat(
                messages
                + [
                    Message("assistant", text),
                    Message(
                        "user",
                        grounding + "\n\nNow answer my previous question from these "
                        "results, in your own style. Mention dates when the results give "
                        "them. Do not output any ACTION line.",
                    ),
                ],
                user_id=user.id,
                max_tokens=1500,
                prefer=_prefer(body),
            )
            text = _SEARCH.sub("", response.text.strip()).strip()
            provider = response.provider
        except Exception as exc:  # noqa: BLE001 — degrade, never lose the turn
            log.warning("chat_search_failed", error=str(exc)[:200])
            text = (
                "I could not reach a model to summarise live results just now — the free "
                "quota looks spent. Try again shortly."
            )

    # ── the agent: anything with an effect, or anything about the user's own rows ──
    if match := _AGENT.search(text):
        try:
            request = str(json.loads(match.group(1)).get("request", "")).strip() or latest
        except json.JSONDecodeError:
            request = latest
        try:
            handle = await _run_agent(session, user.id, request, llm)
            text = describe(handle)
            action = {
                "kind": "agent.run",
                "run_id": str(handle.run_id),
                "status": handle.status,
                "approval_id": (handle.interrupt or {}).get("approval_id"),
            }
        except Exception as exc:  # noqa: BLE001 — a model outage must not lose the turn
            log.warning("chat_agent_failed", error=str(exc)[:200])
            text = (
                "I could not reach a model to do that just now — the free quota looks "
                "spent. Try again shortly, or add a working key in Settings."
            )
            action = None

    # ── video render action ────────────────────────────────────────────
    if match := _GENERATE.search(text):
        text = _GENERATE.sub("", text).strip()
        try:
            topic = str(json.loads(match.group(1)).get("topic", "")).strip()
        except json.JSONDecodeError:
            topic = ""
        if len(topic) >= 3:
            job = await JobQueue(session).enqueue(
                "youtube.generate",
                {"topic": topic, "user_id": str(user.id)},
                user_id=user.id,
                max_attempts=2,
            )
            action = {"kind": "youtube.generate", "topic": topic, "job_id": str(job.id)}

    try:
        await _remember(memory, llm, user.id, latest)
    except Exception as exc:  # noqa: BLE001 — remembering is a bonus, not the turn
        log.warning("chat_remember_failed", error=str(exc)[:200])

    text = text or "Done."
    meta = {"provider": provider, "searched": searched, "action": action}
    now = datetime.now(UTC)
    session.add(
        ChatMessage(
            id=uuid7(),
            user_id=user.id,
            conversation_id=conversation.id,
            role="user",
            content=latest,
        )
    )
    session.add(
        ChatMessage(
            id=uuid7(),
            user_id=user.id,
            conversation_id=conversation.id,
            role="assistant",
            content=text,
            meta=meta,
        )
    )
    conversation.last_message_at = now
    if conversation.title == "New chat":
        try:
            conversation.title = await _title_for(llm, user.id, latest, text)
        except Exception as exc:  # noqa: BLE001 — a title is cosmetic
            log.warning("chat_title_failed", error=str(exc)[:200])
    await session.flush()

    return {
        "text": text,
        "action": action,
        "searched": searched,
        "provider": provider,
        "conversation_id": str(conversation.id),
        "conversation_title": conversation.title,
    }


@router.post("/chat")
async def chat(body: ChatRequest, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    from jarvis.services.memory import MemoryService

    llm = LLMRouter(session)
    conversation = await _resolve_conversation(session, user.id, body.conversation_id)
    persona = await _persona_for(session, user.id, body, conversation)
    recalled = await MemoryService(session).context_for(user.id, body.messages[-1].content)
    messages = _messages_for(body, recalled, await profile_block(session, user.id), persona)
    response = await llm.chat(messages, user_id=user.id, max_tokens=1500, prefer=_prefer(body))
    return await _finish(
        session, user, body, llm, messages, response.text.strip(), response.provider, conversation
    )


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest, user: CurrentUser, session: SessionDep
) -> StreamingResponse:
    """The reply as it is written. Server-sent events:

    ``delta`` — a piece of text; ``final`` — the stored reply and what it triggered
    (identical to ``POST /v1/chat``'s body); ``error``; ``done``.

    An ``ACTION`` line is never streamed: the first characters are held until it is
    clear the reply is prose, so the user never sees the machinery.
    """
    from jarvis.services.memory import MemoryService

    llm = LLMRouter(session)
    conversation = await _resolve_conversation(session, user.id, body.conversation_id)
    persona = await _persona_for(session, user.id, body, conversation)
    recalled = await MemoryService(session).context_for(user.id, body.messages[-1].content)
    messages = _messages_for(body, recalled, await profile_block(session, user.id), persona)
    prefer = _prefer(body)

    def event(kind: str, **payload: Any) -> str:
        return f"data: {json.dumps({'type': kind, **payload})}\n\n"

    async def generate():  # noqa: ANN202
        buffer = ""
        held = True  # until we know the reply is not an ACTION line
        provider = "stream"
        try:
            async for delta in llm.chat_stream(
                messages, user_id=user.id, max_tokens=1500, prefer=prefer
            ):
                buffer += delta
                if held:
                    if len(buffer.lstrip()) < 7:
                        continue
                    held = False
                    if buffer.lstrip().startswith("ACTION"):
                        held = True  # keep holding for the rest of the reply
                        continue
                    yield event("delta", text=buffer)
                    continue
                yield event("delta", text=delta)
        except Exception as exc:  # noqa: BLE001
            log.warning("chat_stream_failed", error=str(exc)[:200])
            yield event("error", message="I could not reach a model just now.")
            yield event("done")
            return

        provider = getattr(llm, "last_stream_provider", provider)
        try:
            final = await _finish(
                session, user, body, llm, messages, buffer.strip(), provider, conversation
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("chat_finish_failed", error=str(exc)[:200])
            yield event("error", message="I answered but could not save that turn.")
            yield event("done")
            return
        yield event("final", **final)
        yield event("done")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_agent(session, user_id, request: str, llm: LLMRouter):  # noqa: ANN001, ANN202
    """One agent run for a chat request, synchronously, on the process-wide checkpointer."""
    from jarvis.services.agent import AgentRuntime, Observation
    from jarvis.services.agent.executor import ToolExecutor
    from jarvis.services.agent.planner import LLMPlanner
    from jarvis.services.agent.runtime import shared_checkpointer

    runtime = AgentRuntime(
        session,
        planner=LLMPlanner(session, router=llm),
        executor=ToolExecutor(session),
        checkpointer=await shared_checkpointer(),
    )
    return await runtime.start(
        user_id,
        observations=[Observation(source="chat", content=request, trust="trusted")],
        trigger="chat",
    )


_TEACH = re.compile(
    r"^\s*(remember( that)?|from now on|always|never|note that|for future reference)\b", re.I
)


async def _remember(memory, llm: LLMRouter, user_id, said: str) -> None:  # noqa: ANN001
    """Ask a cheap call whether the user just told us something durable, and keep it."""
    if len(said) < 20:
        return
    # A sentence that *starts* as an instruction — "remember that…", "from now on…",
    # "never…" — is training, not chatter: stored verbatim, with weight, no model between.
    if _TEACH.match(said):
        await memory.remember(
            user_id,
            content=said.strip(),
            kind="instruction",
            provenance={"source": "chat", "taught": True},
            importance=0.95,
        )
        return
    try:
        response = await llm.generate(
            LLMRequest(
                call_class=CallClass.CLASSIFY,
                messages=[
                    Message(
                        "system",
                        "Extract durable facts about the user from their message: "
                        "preferences, people, places, recurring commitments, corrections. "
                        "Return at most 3 short third-person sentences. Return an empty "
                        "list for questions, requests, or small talk. JSON only.",
                    ),
                    Message("user", said[:2000]),
                ],
                json_schema=MEMO_SCHEMA,
                max_tokens=200,
                temperature=0.0,
                user_id=user_id,
            )
        )
        facts = json.loads(response.text.strip().strip("`").removeprefix("json")).get("facts", [])
        for fact in [f for f in facts if isinstance(f, str) and 8 < len(f) < 300][:3]:
            await memory.remember(
                user_id,
                content=fact,
                kind="episodic",
                provenance={"source": "chat"},
                importance=0.5,
            )
    except Exception as exc:  # noqa: BLE001 — memory is a bonus, never a failure
        log.info("chat_memory_skipped", error=str(exc)[:120])


async def _title_for(llm: LLMRouter, user_id, asked: str, answered: str) -> str:  # noqa: ANN001
    """Five words that name the thread, from its first exchange."""
    try:
        response = await llm.generate(
            LLMRequest(
                call_class=CallClass.CLASSIFY,
                messages=[
                    Message("system", "Give this chat a title of at most five words. JSON only."),
                    Message("user", f"User: {asked[:500]}\nAssistant: {answered[:500]}"),
                ],
                json_schema=TITLE_SCHEMA,
                max_tokens=40,
                temperature=0.2,
                user_id=user_id,
            )
        )
        title = str(
            json.loads(response.text.strip().strip("`").removeprefix("json")).get("title", "")
        )
        return title.strip().strip('"')[:120] or asked[:60]
    except Exception:  # noqa: BLE001
        return asked[:60]
