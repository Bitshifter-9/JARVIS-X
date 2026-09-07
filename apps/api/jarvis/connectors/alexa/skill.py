"""The seven intents, as plain functions over the same services the app uses.

Nothing here is Alexa-specific except the response envelope. An intent handler calls
``GoalService`` / ``ModuleService`` / ``ToolGateway`` exactly as the REST routes do, so a
voice command cannot reach a capability the app does not already expose — and cannot skip
the policy engine to do it.

**Voice is a control surface, not a credential.** A request without a linked account gets
a LinkAccount card and nothing else; an approval spoken aloud is still an ``approvals``
row with a payload hash, decided through the same gateway as the Telegram button.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.core.logging import get_logger
from jarvis.db.models.agent import Approval
from jarvis.db.models.domain import Task
from jarvis.services.goal import GoalService
from jarvis.services.modules import ModuleService
from jarvis.services.tool_gateway import ToolGateway
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)


# ── response envelope ──────────────────────────────────────────────────
def speak(text: str, *, end_session: bool = True, reprompt: str | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {
        "outputSpeech": {"type": "PlainText", "text": text},
        "shouldEndSession": end_session,
    }
    if reprompt:
        response["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt}}
    return {"version": "1.0", "response": response}


def link_account() -> dict[str, Any]:
    """The only correct answer to an unlinked request (blueprint §16)."""
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {
                "type": "PlainText",
                "text": "Please link your JARVIS account in the Alexa app, then try again.",
            },
            "card": {"type": "LinkAccount"},
            "shouldEndSession": True,
        },
    }


def confirm(text: str) -> dict[str, Any]:
    """Ask Alexa to run its own confirmation turn before the intent is acted on."""
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": text},
            "directives": [{"type": "Dialog.ConfirmIntent"}],
            "shouldEndSession": False,
        },
    }


# ── slots ──────────────────────────────────────────────────────────────
def slot(request: dict[str, Any], name: str) -> str | None:
    value = ((request.get("intent") or {}).get("slots") or {}).get(name) or {}
    text = value.get("value")
    return str(text) if text else None


def _due_at(date: str | None, time_of_day: str | None, tz: ZoneInfo) -> datetime | None:
    """AMAZON.DATE + AMAZON.TIME → one aware instant in the user's timezone.

    A date with no time means the end of that day, not its start: "due Friday" that
    resolves to 00:00 would fire every reminder a day early.
    """
    if not date:
        return None
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()  # noqa: DTZ007 — date only
    except ValueError:
        return None
    hour, minute = 23, 59
    if time_of_day:
        try:
            parts = time_of_day.split(":")
            hour, minute = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(UTC)


async def _timezone(session: AsyncSession, user_id: uuid.UUID) -> ZoneInfo:
    from jarvis.core.config import get_settings
    from jarvis.db.models.identity import User

    user = await session.get(User, user_id)
    return ZoneInfo((user.timezone if user else None) or get_settings().timezone)


def _spoken_time(when: datetime, tz: ZoneInfo) -> str:
    return when.astimezone(tz).strftime("%A at %-I:%M %p")


# ── intents ────────────────────────────────────────────────────────────
async def what_is_due(session: AsyncSession, user_id: uuid.UUID, request: dict) -> dict:  # noqa: ARG001
    brief = await ModuleService(session).morning_brief(user_id)
    if not brief.due_today:
        return speak("Nothing is due today.")
    tz = await _timezone(session, user_id)
    lines = [
        f"{t.title}{'' if t.due_at is None else ', ' + _spoken_time(t.due_at, tz)}"
        for t in brief.due_today[:5]
    ]
    return speak(f"{len(brief.due_today)} due today. " + ". ".join(lines) + ".")


async def morning_brief(session: AsyncSession, user_id: uuid.UUID, request: dict) -> dict:  # noqa: ARG001
    brief = await ModuleService(session).morning_brief(user_id)
    first = brief.suggested_first.title if brief.suggested_first else None
    text = f"{brief.greeting}. {brief.headline}"
    if first:
        text += f" Start with {first}."
    return speak(text)


async def add_task(session: AsyncSession, user_id: uuid.UUID, request: dict) -> dict:
    title = slot(request, "taskTitle")
    if not title:
        return speak(
            "What should I call the task?",
            end_session=False,
            reprompt="Say the task name.",
        )

    tz = await _timezone(session, user_id)
    due_at = _due_at(slot(request, "dueDate"), slot(request, "dueTime"), tz)
    task = await GoalService(session).create_task(
        user_id, title=title, due_at=due_at, timezone=str(tz)
    )
    when = f" due {_spoken_time(task.due_at, tz)}" if task.due_at else ""
    return speak(f"Added {title}{when}.")


async def risk_check(session: AsyncSession, user_id: uuid.UUID, request: dict) -> dict:
    goals = GoalService(session)
    wanted = (slot(request, "goalName") or "").lower()
    active = await goals.list_goals(user_id, status="active")
    match = next((g for g in active if wanted and wanted in g.title.lower()), None) or (
        active[0] if active else None
    )
    if match is None:
        return speak("You have no active goals.")

    prediction = await goals.predict_goal(user_id, match.id, persist=False)
    severity = prediction.severity.replace("_", " ")
    return speak(f"{match.title} is at {prediction.probability:.0%} — {severity}.")


async def acknowledge(session: AsyncSession, user_id: uuid.UUID, request: dict) -> dict:
    wanted = (slot(request, "taskTitle") or "").lower()
    task = await _find_open_task(session, user_id, wanted)
    if task is None:
        return speak("I could not find that task.")
    cancelled = await GoalService(session).acknowledge_task(user_id, task.id)
    return speak(f"Acknowledged {task.title}. {cancelled} later alert or alerts cancelled.")


async def start_focus(session: AsyncSession, user_id: uuid.UUID, request: dict) -> dict:
    wanted = (slot(request, "taskTitle") or "").lower()
    task = await _find_open_task(session, user_id, wanted)
    if task is None:
        return speak("I could not find a task to focus on.")
    focus = await ModuleService(session).start_focus(user_id, task.id)
    return speak(f"Focusing on {focus.title} for {focus.planned_minutes} minutes.")


async def approve_pending(session: AsyncSession, user_id: uuid.UUID, request: dict) -> dict:
    """Approve the one pending action, but only after Alexa has confirmed the intent.

    Voice is the easiest surface to trigger by accident — a television can say "approve"
    — so the confirmation turn is not optional here, and the action is read back in full
    before it happens.
    """
    from jarvis.db.models.agent import Action

    pending = list(
        (
            await session.scalars(
                select(Approval)
                .where(
                    Approval.user_id == user_id,
                    Approval.decision.is_(None),
                    Approval.expires_at > datetime.now(UTC),
                )
                .order_by(Approval.created_at.desc())
                .limit(2)
            )
        ).all()
    )
    if not pending:
        return speak("There is nothing waiting for approval.")
    if len(pending) > 1:
        return speak(
            "You have more than one pending approval. Please decide them in the app, "
            "where you can see exactly what each one does."
        )

    approval = pending[0]
    action = await session.get(Action, approval.action_id)
    description = action.tool if action else "the pending action"

    if approval.requires_local_confirmation:
        return speak(
            f"{description} also needs confirmation on your Mac, so I cannot approve it by voice."
        )

    status = (request.get("intent") or {}).get("confirmationStatus")
    if status != "CONFIRMED":
        if status == "DENIED":
            return speak("Left it pending.")
        return confirm(f"Do you want me to approve {description}?")

    await ToolGateway(session).decide(
        user_id, approval.id, approved=True, decided_by="alexa"
    )
    return speak(f"Approved {description}.")


async def _find_open_task(
    session: AsyncSession, user_id: uuid.UUID, wanted: str
) -> Task | None:
    """The named open task, or the one due soonest when nothing was named."""
    tasks = list(
        (
            await session.scalars(
                select(Task)
                .where(Task.user_id == user_id, Task.status.in_(["open", "in_progress"]))
                .order_by(Task.due_at.nulls_last())
                .limit(50)
            )
        ).all()
    )
    if wanted:
        return next((t for t in tasks if wanted in t.title.lower()), None)
    return tasks[0] if tasks else None


INTENTS = {
    "WhatIsDueIntent": what_is_due,
    "MorningBriefIntent": morning_brief,
    "AddTaskIntent": add_task,
    "RiskCheckIntent": risk_check,
    "AcknowledgeTaskIntent": acknowledge,
    "StartFocusIntent": start_focus,
    "ApprovePendingIntent": approve_pending,
}

HELP = (
    "You can ask what is due today, add a task, ask whether a goal is at risk, "
    "acknowledge a task, start a focus session, or approve a pending action."
)


async def handle_request(
    session: AsyncSession, body: dict[str, Any], *, user_id: uuid.UUID | None
) -> dict[str, Any]:
    """Route one Alexa request. ``user_id`` is ``None`` when the account is not linked."""
    request = body.get("request") or {}
    kind = request.get("type")

    if kind == "SessionEndedRequest":
        return {"version": "1.0", "response": {"shouldEndSession": True}}

    if user_id is None:
        return link_account()

    if kind == "LaunchRequest":
        return await morning_brief(session, user_id, request)

    if kind != "IntentRequest":
        return speak(HELP)

    name = (request.get("intent") or {}).get("name", "")
    if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
        return speak("Okay.")
    if name in ("AMAZON.HelpIntent", "AMAZON.FallbackIntent"):
        return speak(HELP, end_session=False, reprompt=HELP)

    handler = INTENTS.get(name)
    if handler is None:
        log.info("alexa_unknown_intent", intent=name)
        return speak(HELP, end_session=False, reprompt=HELP)

    return await handler(session, user_id, request)


__all__ = ["HELP", "INTENTS", "confirm", "handle_request", "link_account", "speak"]
