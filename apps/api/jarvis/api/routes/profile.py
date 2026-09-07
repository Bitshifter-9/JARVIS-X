"""Training Jarvis: the profile, feedback on replies, and learning how you write.

Three inputs, in increasing order of how much they change the model's behaviour:

* **Profile** — what you wrote about yourself; on every prompt, verbatim.
* **Feedback** — a thumbs down with a reason becomes a ``feedback`` memory the model
  is reminded of when a similar question comes up.
* **Style** — with your click, your own sent mail is summarised into a style card
  (tone, length, sign-off, phrases you use) so drafts read like you wrote them.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.errors import Conflict
from jarvis.core.logging import get_logger
from jarvis.db.models.chat import ChatMessage
from jarvis.db.models.ops import ChatFeedback
from jarvis.llm.router import LLMRouter
from jarvis.llm.types import CallClass, LLMRequest, Message
from jarvis.services.event.service import EventService
from jarvis.services.memory import MemoryService
from jarvis.services.profile import SECTIONS, get_profile, render

log = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["profile"])

STYLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"style": {"type": "string"}},
    "required": ["style"],
}


def _out(profile) -> dict[str, Any]:  # noqa: ANN001
    return {key: getattr(profile, key) for key, _ in SECTIONS} | {
        "rendered": render(profile),
        "updated_at": (
            profile.updated_at.isoformat() if "updated_at" in profile.__dict__ else None
        ),
    }


class ProfileIn(BaseModel):
    about: str | None = Field(default=None, max_length=4000)
    priorities: str | None = Field(default=None, max_length=4000)
    people: str | None = Field(default=None, max_length=4000)
    style: str | None = Field(default=None, max_length=2000)
    decisions: str | None = Field(default=None, max_length=4000)
    learned_style: str | None = Field(default=None, max_length=4000)


@router.get("/profile")
async def read_profile(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return _out(await get_profile(session, user.id))


@router.put("/profile")
async def write_profile(body: ProfileIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    profile = await get_profile(session, user.id)
    for key, _ in SECTIONS:
        value = getattr(body, key)
        if value is not None:
            setattr(profile, key, value.strip())
    await session.flush()
    return _out(profile)


@router.post("/profile/learn-style")
async def learn_style(user: CurrentUser, session: SessionDep, limit: int = 20) -> dict[str, Any]:
    """Read your recent sent mail (on request only) and write the style card."""
    from jarvis.connectors.google.gmail import GmailConnector
    from jarvis.connectors.google.oauth import TokenStore

    store = TokenStore(session)
    accounts = await store.list(user.id, "gmail")
    if not accounts:
        raise Conflict("Connect a Google account first")
    samples: list[str] = []
    for account in accounts:
        for item in await GmailConnector(store).list_sent(
            account.id, limit=max(3, limit // len(accounts))
        ):
            samples.append(f"Subject: {item.title}\n{(item.body or '')[:1500]}")
    if not samples:
        raise Conflict("No sent mail with text was found")

    response = await LLMRouter(session).generate(
        LLMRequest(
            call_class=CallClass.REFLECT,
            messages=[
                Message(
                    "system",
                    "You are describing how one person writes, from samples of their own "
                    "email. Write a compact style card (under 180 words) covering: tone, "
                    "typical length, greeting and sign-off, sentence habits, words and "
                    "phrases they favour, how they handle requests and apologies. Third "
                    "person. No quotes longer than five words. JSON only.",
                ),
                Message("user", EventService.untrusted("\n\n---\n\n".join(samples[:20]))),
            ],
            json_schema=STYLE_SCHEMA,
            max_tokens=500,
            temperature=0.2,
            user_id=user.id,
        )
    )
    style = str(
        json.loads(response.text.strip().strip("`").removeprefix("json")).get("style", "")
    ).strip()
    profile = await get_profile(session, user.id)
    profile.learned_style = style[:4000]
    await session.flush()
    return {"samples": len(samples), "learned_style": profile.learned_style}


# ── the 3-minute interview (PLAN.md 10.3.3) ───────────────────────────────────────
INTERVIEW = [
    ("about", "Who are you, in a few lines? What do you do, study or build?"),
    ("priorities", "What matters most to you right now — the next month or two?"),
    (
        "people",
        "Who will I hear about — family, professors, teammates, clients — and how should "
        "I refer to them?",
    ),
    (
        "style",
        "How should I talk to you? Short or thorough, formal or casual, anything to avoid?",
    ),
    (
        "decisions",
        "How do you make decisions? What may I do on my own, and what must I always ask "
        "you first?",
    ),
    ("rules", "Anything I should never do, or always remember?"),
]
INTERVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {k: {"type": "string"} for k, _ in SECTIONS[:5]},
    "required": [k for k, _ in SECTIONS[:5]],
}


class InterviewIn(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)


@router.get("/profile/phrasebook")
async def get_phrasebook(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Your recurring names, jargon and acronyms, learned from your own words (#13). Free —
    proper-noun frequency, no model. What the transcriber and drafter should never mishear."""
    from jarvis.services.phrasebook import phrasebook

    return {"terms": await phrasebook(session, user.id)}


@router.get("/profile/speech")
async def speech(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """How you phrase things (#12): filler words, favourite phrases, sentence length — from
    your own messages, no model. Feeds drafting-in-your-voice and the coach."""
    from jarvis.services.speech import speech_profile

    return await speech_profile(session, user.id)


@router.get("/profile/knowledge-gaps")
async def knowledge_gaps_route(
    user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """Topics you keep asking about (#29) — likely gaps worth a micro-lesson."""
    from jarvis.services.knowledge_gaps import knowledge_gaps

    return {"gaps": await knowledge_gaps(session, user.id)}


@router.get("/profile/interview")
async def interview_questions(user: CurrentUser) -> list[dict[str, str]]:  # noqa: ARG001
    return [{"key": k, "question": q} for k, q in INTERVIEW]


@router.post("/profile/interview")
async def interview(body: InterviewIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Six answers in, five profile sections out — distilled by the model when one is
    reachable, written verbatim when not. Hard rules also become standing instructions."""
    answers = {k: (body.answers.get(k) or "").strip() for k, _ in INTERVIEW}
    if not any(answers.values()):
        raise Conflict("Answer at least one question")
    transcript = "\n\n".join(
        f"Q: {q}\nA: {answers[k]}" for k, q in INTERVIEW if answers[k]
    )
    fields = {k: answers[k] for k, _ in SECTIONS[:5]}
    if answers["rules"]:
        fields["decisions"] = (
            f"{fields['decisions']}\n\nHard rules:\n{answers['rules']}".strip()
        )
    distilled = False
    try:
        response = await LLMRouter(session).generate(
            LLMRequest(
                call_class=CallClass.REFLECT,
                messages=[
                    Message(
                        "system",
                        "You are writing a personal assistant's profile of its owner from an "
                        "interview. Fill five sections — about, priorities, people, style, "
                        "decisions — each a compact paragraph in the second person ('You "
                        "are…'), keeping every name, preference and rule stated. Put any "
                        "hard rules at the end of 'decisions' under 'Hard rules:'. Leave a "
                        "section empty if nothing was said about it. JSON only.",
                    ),
                    Message("user", EventService.untrusted(transcript)),
                ],
                json_schema=INTERVIEW_SCHEMA,
                max_tokens=900,
                temperature=0.2,
                user_id=user.id,
            )
        )
        parsed = json.loads(response.text.strip().strip("`").removeprefix("json"))
        if isinstance(parsed, dict) and any(str(v).strip() for v in parsed.values()):
            fields = {k: str(parsed.get(k) or fields[k]).strip() for k in fields}
            distilled = True
    except Exception as exc:  # noqa: BLE001 — verbatim answers are a fine profile too
        log.warning("interview_distillation_failed", error=str(exc)[:200])
    profile = await get_profile(session, user.id)
    for key, value in fields.items():
        if value:
            setattr(profile, key, value[:4000])
    if answers["rules"]:
        await MemoryService(session).remember(
            user.id,
            kind="instruction",
            content=answers["rules"][:2000],
            provenance={"source": "interview"},
            importance=0.95,
        )
    await session.flush()
    return _out(profile) | {"distilled": distilled}


# ── what the nightly loop proposes (PLAN.md 10.6.5) ────────────────────────────────
@router.get("/profile/suggestions")
async def suggestions(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    profile = await get_profile(session, user.id)
    return list((profile.suggestions or {}).get("pending") or [])


@router.post("/profile/suggestions/{suggestion_id}/{verdict}")
async def resolve(
    suggestion_id: str, verdict: str, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    from jarvis.core.errors import NotFound
    from jarvis.services.activity import resolve_suggestion

    if verdict not in ("accept", "dismiss"):
        raise NotFound("Verdict")
    profile = await get_profile(session, user.id)
    match = resolve_suggestion(profile, suggestion_id, accept=verdict == "accept")
    if match is None:
        raise NotFound("Suggestion")
    await session.flush()
    return _out(profile) | {"resolved": match, "verdict": verdict}


class FeedbackIn(BaseModel):
    message_id: str | None = None
    score: int = Field(ge=-1, le=1)
    note: str | None = Field(default=None, max_length=1000)
    excerpt: str | None = Field(default=None, max_length=500)


@router.post("/chat/feedback", status_code=201)
async def feedback(body: FeedbackIn, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    import uuid

    excerpt = body.excerpt
    message_id = None
    if body.message_id:
        try:
            message_id = uuid.UUID(body.message_id)
            message = await session.get(ChatMessage, message_id)
            if message is not None and message.user_id == user.id and not excerpt:
                excerpt = message.content[:300]
            if message is None or message.user_id != user.id:
                message_id = None
        except ValueError:
            message_id = None

    row = ChatFeedback(
        user_id=user.id,
        message_id=message_id,
        score=body.score,
        note=(body.note or "").strip() or None,
        excerpt=excerpt,
    )
    session.add(row)

    # A reason is training. Kept as a memory the model is reminded of when the same
    # ground comes up again; a bare thumbs is only a statistic.
    if row.note and row.score < 0:
        await MemoryService(session).remember(
            user.id,
            content=(
                "Correction from the user: when replying about "
                f'"{(excerpt or "")[:120]}" — {row.note}'
            ),
            kind="feedback",
            provenance={"source": "feedback"},
            importance=0.85,
        )
    elif row.note and row.score > 0:
        await MemoryService(session).remember(
            user.id,
            content=f"The user liked this kind of reply: {row.note}",
            kind="feedback",
            provenance={"source": "feedback"},
            importance=0.6,
        )
    await session.flush()
    return {"id": str(row.id), "score": row.score, "learned": bool(row.note)}


@router.get("/profile/feedback")
async def feedback_stats(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    up = await session.scalar(
        select(func.count())
        .select_from(ChatFeedback)
        .where(ChatFeedback.user_id == user.id, ChatFeedback.score > 0)
    )
    down = await session.scalar(
        select(func.count())
        .select_from(ChatFeedback)
        .where(ChatFeedback.user_id == user.id, ChatFeedback.score < 0)
    )
    recent = (
        await session.scalars(
            select(ChatFeedback)
            .where(ChatFeedback.user_id == user.id, ChatFeedback.note.isnot(None))
            .order_by(ChatFeedback.created_at.desc())
            .limit(10)
        )
    ).all()
    return {
        "up": up or 0,
        "down": down or 0,
        "lessons": [
            {"score": f.score, "note": f.note, "excerpt": f.excerpt, "at": f.created_at.isoformat()}
            for f in recent
        ],
    }
