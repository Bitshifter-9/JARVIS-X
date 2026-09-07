"""Briefings (PLAN.md 10.1, fixed): a routine that only *tells* you things is answered by
the chat model from context the server gathered — due dates with their sources, what is
at risk, recent mail, the weather, today's screen time — not by the planner's JSON
``answer`` field, which is built for deciding steps and writes poor prose.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.db.models.domain import Task
from jarvis.db.models.identity import User
from jarvis.db.models.source import SourceObject
from jarvis.llm.types import Message
from jarvis.services.profile import profile_block
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)


async def gather(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> dict[str, Any]:
    """Everything a briefing may cite, as plain data."""
    from jarvis.services import activity
    from jarvis.services.modules import ModuleService

    moment = now or datetime.now(UTC)
    user = await session.get(User, user_id)
    zone = ZoneInfo((user.timezone if user else None) or get_settings().timezone)
    local = moment.astimezone(zone)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)

    tasks = (
        await session.execute(
            select(Task, SourceObject)
            .outerjoin(SourceObject, SourceObject.id == Task.source_id)
            .where(
                Task.user_id == user_id,
                Task.status.in_(("open", "in_progress")),
                Task.due_at.is_not(None),
                Task.due_at <= moment + timedelta(days=7),
            )
            .order_by(Task.due_at)
            .limit(12)
        )
    ).all()
    due = [
        {
            "title": t.title,
            "due": t.due_at.astimezone(zone).strftime("%a %d %b %H:%M"),
            "from": f"{s.provider} · {s.author}" if s else "you",
        }
        for t, s in tasks
    ]
    try:
        brief = await ModuleService(session).morning_brief(user_id, now=moment)
        at_risk = [
            {"goal": r.title, "probability": round(r.probability, 2), "why": r.explanation[:160]}
            for r in brief.at_risk
        ]
        first = brief.suggested_first.title if brief.suggested_first else None
    except Exception as exc:  # noqa: BLE001
        log.warning("briefing_risk_failed", error=str(exc)[:120])
        at_risk, first = [], None
    mail = (
        await session.scalars(
            select(SourceObject)
            .where(SourceObject.user_id == user_id, SourceObject.provider.in_(("gmail", "slack")))
            .order_by(SourceObject.occurred_at.desc())
            .limit(6)
        )
    ).all()
    recent = [
        {
            "from": m.author,
            "subject": m.title,
            "when": (m.occurred_at or moment).astimezone(zone).strftime("%a %H:%M"),
        }
        for m in mail
    ]
    weather: dict[str, Any] | None = None
    if get_settings().owner_location:
        try:
            from jarvis.services.world import weather as world_weather

            weather = await world_weather(get_settings().owner_location)
        except Exception as exc:  # noqa: BLE001
            log.warning("briefing_weather_failed", error=str(exc)[:120])
    screen = await activity.summary(session, user_id, day_start, moment)
    return {
        "now": local.strftime("%A %d %B %Y, %H:%M"),
        "due_within_7_days": due,
        "at_risk": at_risk,
        "suggested_first_task": first,
        "recent_mail": recent,
        "weather": weather,
        "screen_time_today": screen[:6],
    }


def fallback(context: dict[str, Any]) -> str:
    """Spoken-plain prose with no model at all — never an empty briefing."""
    due = context.get("due_within_7_days") or []
    parts = []
    if due:
        first = due[0]
        parts.append(
            f"{len(due)} thing(s) due this week; first is {first['title']} on {first['due']}."
        )
    else:
        parts.append("Nothing is due this week.")
    if context.get("at_risk"):
        parts.append(f"{context['at_risk'][0]['goal']} is at risk.")
    if context.get("weather") and "temperature_c" in context["weather"]:
        w = context["weather"]
        parts.append(f"It is {w['temperature_c']}° and {w['summary']} in {w['place']}.")
    return " ".join(parts)


async def compose(
    session: AsyncSession,
    user_id: uuid.UUID,
    prompt: str,
    *,
    router=None,  # noqa: ANN001
    now: datetime | None = None,
) -> str:
    from jarvis.api.routes.chat import _persona
    from jarvis.llm.router import LLMRouter

    context = await gather(session, user_id, now=now)
    system = (
        _persona()
        + "\n\nYou are composing a BRIEFING that will be read aloud. Plain sentences, no "
        "markdown, no headings, no lists, no preamble — start with the first fact. Only cite "
        "what is in the context; say plainly when something is empty. Under 130 words."
        + "\n\n"
        + await profile_block(session, user_id)
    )
    llm = router or LLMRouter(session)
    try:
        response = await llm.chat(
            [
                Message("system", system),
                Message(
                    "user",
                    f"{prompt}\n\nContext (data):\n{json.dumps(context, default=str)[:6000]}",
                ),
            ],
            user_id=user_id,
            max_tokens=400,
        )
        text = response.text.strip().replace("**", "").replace("#", "").strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("briefing_model_failed", error=str(exc)[:160])
        text = ""
    if len(text) < 40 or text.endswith((":", "):")):
        text = fallback(context)
    return text[:2000]
