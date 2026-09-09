"""Activity, the focus guard, and the learning loop (PLAN.md 10.6.3–10.6.5).

Samples are app and window titles, never screenshots. The guard fires only inside a
focus block the owner started, climbs three rungs (notify → speak → lock), and every
rung is a policy-gated verb on the Timeline. The nightly loop only *suggests*; the
profile changes when the owner taps accept.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.db.models.agent import Action, Approval
from jarvis.db.models.domain import WorkSession
from jarvis.db.models.identity import User
from jarvis.db.models.ops import ActivitySample, AuditLog, ChatFeedback, Profile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

RETENTION_DAYS = 30
SAMPLE_SECONDS = 30
NUDGE_TOOLS = {"macos": ("mac.notify", "mac.say", "mac.lock_screen"), "android": ("phone.notify",)}


# ── samples ───────────────────────────────────────────────────────────
async def record(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    device_id: uuid.UUID | None,
    platform: str,
    samples: list[dict[str, Any]],
) -> int:
    stored = 0
    for raw in samples[:500]:
        app = str(raw.get("app") or "").strip()[:200]
        if not app:
            continue
        try:
            at = datetime.fromisoformat(str(raw["at"])) if raw.get("at") else datetime.now(UTC)
        except (ValueError, KeyError):
            at = datetime.now(UTC)
        session.add(
            ActivitySample(
                user_id=user_id,
                device_id=device_id,
                platform=platform,
                app=app,
                title=(str(raw.get("title") or "")[:300] or None),
                at=at,
            )
        )
        stored += 1
    await session.flush()
    return stored


async def prune(session: AsyncSession) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    result = await session.execute(delete(ActivitySample).where(ActivitySample.at < cutoff))
    return int(result.rowcount or 0)


async def summary(
    session: AsyncSession, user_id: uuid.UUID, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Minutes per app in a window, with a few titles — the answer to "what was I doing"."""
    rows = (
        await session.scalars(
            select(ActivitySample)
            .where(
                ActivitySample.user_id == user_id,
                ActivitySample.at >= start,
                ActivitySample.at < end,
            )
            .order_by(ActivitySample.at)
        )
    ).all()
    minutes: dict[str, float] = defaultdict(float)
    titles: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        minutes[row.app] += SAMPLE_SECONDS / 60
        if row.title and row.title not in titles[row.app] and len(titles[row.app]) < 3:
            titles[row.app].append(row.title)
    return sorted(
        (
            {"app": app, "minutes": round(m), "titles": titles[app]}
            for app, m in minutes.items()
            if round(m) > 0
        ),
        key=lambda x: -x["minutes"],
    )


# ── the focus guard ───────────────────────────────────────────────────
def distracting_terms() -> list[str]:
    raw = get_settings().focus_distracting_apps.split(",")
    return [t.strip().lower() for t in raw if t.strip()]


def is_distracting(sample: ActivitySample, terms: list[str]) -> bool:
    hay = f"{sample.app} {sample.title or ''}".lower()
    return any(t in hay for t in terms)


async def active_focus_block(session: AsyncSession, user_id: uuid.UUID, now: datetime):  # noqa: ANN201
    """The focus session the owner started and has not ended, if it is still fresh."""
    return await session.scalar(
        select(WorkSession)
        .where(
            WorkSession.user_id == user_id,
            WorkSession.source == "focus",
            WorkSession.ended_at.is_(None),
            WorkSession.started_at >= now - timedelta(hours=3),
        )
        .order_by(WorkSession.started_at.desc())
        .limit(1)
    )


async def focus_guard(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> dict[str, Any]:
    """One check: inside a focus block, has the owner drifted for too long? Then one
    rung — a notification, then a spoken nudge, then the lock — through the gate."""
    s = get_settings()
    if not s.focus_guard_enabled:
        return {"skipped": "off"}
    moment = now or datetime.now(UTC)
    block = await active_focus_block(session, user_id, moment)
    if block is None:
        return {"skipped": "no focus block"}
    window = moment - timedelta(minutes=s.focus_guard_minutes)
    samples = (
        await session.scalars(
            select(ActivitySample)
            .where(ActivitySample.user_id == user_id, ActivitySample.at >= window)
            .order_by(ActivitySample.at)
        )
    ).all()
    expected = max(1, int(s.focus_guard_minutes * 60 / SAMPLE_SECONDS * 0.6))
    terms = distracting_terms()
    off_task = [x for x in samples if is_distracting(x, terms)]
    if len(samples) < expected or len(off_task) < len(samples) * 0.8:
        return {"skipped": "on task", "samples": len(samples), "off_task": len(off_task)}

    latest = off_task[-1]
    nudges = int(
        await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.user_id == user_id,
                AuditLog.action == "focus.nudged",
                AuditLog.subject_id == str(block.id),
            )
        )
        or 0
    )
    tools = NUDGE_TOOLS.get(latest.platform, NUDGE_TOOLS["macos"])
    tool = tools[min(nudges, len(tools) - 1)]
    minutes = round(len(off_task) * SAMPLE_SECONDS / 60)
    # A cognitive pause, not a scold. The craving is the target, not the willpower:
    # catch it, name the feeling, insert a tiny interrupt, then leave the choice to the
    # owner. Nagging strengthens the shame half of the loop and changes nothing.
    text = (
        f"{minutes} minutes on {latest.app}. Before anything else — what are you feeling? "
        "Close your eyes for 60 seconds. Then it's your call."
    )
    args = {
        "mac.notify": {"title": "Focus", "body": text},
        "phone.notify": {"title": "Focus", "body": text},
        "mac.say": {"text": text},
        "mac.lock_screen": {},
    }[tool]

    from jarvis.services.agent.executor import dispatch_action
    from jarvis.services.tool_gateway import ToolGateway

    proposal = await ToolGateway(session).propose(
        user_id,
        tool=tool,
        args=args,
        device_id=latest.device_id,
        rationale=f"focus guard rung {nudges + 1}: {minutes} min on {latest.app}",
    )
    dispatched: dict[str, Any] = {"status": proposal.action.status}
    if not proposal.needs_approval and proposal.policy.decision.value != "deny":
        dispatched = await dispatch_action(session, proposal.action.id)
    session.add(
        AuditLog(
            user_id=user_id,
            actor="system",
            action="focus.nudged",
            subject_type="work_session",
            subject_id=str(block.id),
            detail={"tier": nudges + 1, "tool": tool, "app": latest.app, "minutes": minutes},
        )
    )
    await session.flush()
    log.info("focus_nudged", tier=nudges + 1, tool=tool, app=latest.app)
    return {
        "nudged": True,
        "tier": nudges + 1,
        "tool": tool,
        "action_id": str(proposal.action.id),
        "dispatch": dispatched.get("status") or dispatched.get("verdict"),
    }


# ── the learning loop ─────────────────────────────────────────────────
SUGGESTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "section": {
                        "type": "string",
                        "enum": ["about", "priorities", "people", "style", "decisions"],
                    },
                    "text": {"type": "string"},
                    "because": {"type": "string"},
                },
                "required": ["section", "text", "because"],
            },
        }
    },
    "required": ["suggestions"],
}


async def _learned_today(session: AsyncSession, user_id: uuid.UUID, day_start: datetime) -> bool:
    return bool(
        await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.user_id == user_id,
                AuditLog.action == "learning.distilled",
                AuditLog.created_at >= day_start,
            )
        )
    )


async def learn(
    session: AsyncSession, user_id: uuid.UUID, *, router=None, now: datetime | None = None
) -> dict[str, Any]:  # noqa: ANN001
    """Distil the day into a few profile suggestions — once per local day, after 21:00,
    never silently applied (PLAN.md 10.6.5)."""
    moment = now or datetime.now(UTC)
    user = await session.get(User, user_id)
    zone = ZoneInfo((user.timezone if user else None) or get_settings().timezone)
    local = moment.astimezone(zone)
    if local.hour < 21:
        return {"skipped": "too early"}
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    if await _learned_today(session, user_id, day_start):
        return {"skipped": "done today"}

    activity = await summary(session, user_id, day_start, moment)
    decided = (
        await session.execute(
            select(Action.tool, Approval.decision, func.count())
            .join(Action, Action.id == Approval.action_id)
            .where(Approval.user_id == user_id, Approval.decided_at >= day_start)
            .group_by(Action.tool, Approval.decision)
        )
    ).all()
    feedback = (
        await session.scalars(
            select(ChatFeedback).where(
                ChatFeedback.user_id == user_id, ChatFeedback.created_at >= day_start
            )
        )
    ).all()
    evidence = {
        "activity_minutes_by_app": activity[:12],
        "approvals": [{"tool": t, "decision": d, "count": c} for t, d, c in decided],
        "feedback": [{"score": f.score, "note": f.note} for f in feedback][:20],
    }
    if not any(evidence.values()):
        return {"skipped": "nothing happened"}

    profile = await session.get(Profile, user_id)
    sections = ("about", "priorities", "people", "style", "decisions")
    current = {k: getattr(profile, k, "") for k in sections} if profile else {}
    from jarvis.llm.router import LLMRouter
    from jarvis.llm.types import CallClass, LLMRequest, Message

    llm = router or LLMRouter(session)
    response = await llm.generate(
        LLMRequest(
            call_class=CallClass.REFLECT,
            messages=[
                Message(
                    "system",
                    "You maintain a personal assistant's profile of its owner. From one day's "
                    "evidence — minutes per app, which proposed actions they approved or "
                    "rejected, and their feedback on replies — propose at most four short, "
                    "concrete profile additions (second person, one sentence each) that would "
                    "make the assistant act more like they want. Only patterns the evidence "
                    "supports; nothing already in the profile; nothing about a single "
                    "incident. JSON only.",
                ),
                Message(
                    "user",
                    f"Current profile:\n{json.dumps(current)[:2500]}\n\n"
                    f"Today's evidence:\n{json.dumps(evidence, default=str)[:4000]}",
                ),
            ],
            json_schema=SUGGESTION_SCHEMA,
            max_tokens=700,
            temperature=0.3,
            user_id=user_id,
        )
    )
    try:
        parsed = json.loads(response.text.strip().strip("`").removeprefix("json"))
    except ValueError:
        parsed = {"suggestions": []}
    if profile is None:
        from jarvis.services.profile import get_profile

        profile = await get_profile(session, user_id)
    box = dict(profile.suggestions or {})
    pending: list[dict[str, Any]] = list(box.get("pending") or [])
    dismissed = {d.get("text") for d in (box.get("dismissed") or [])}
    known = {p.get("text") for p in pending} | dismissed
    added = 0
    for item in (parsed.get("suggestions") or [])[:4]:
        text = str(item.get("text") or "").strip()
        if not text or text in known:
            continue
        pending.append(
            {
                "id": uuid.uuid4().hex[:12],
                "section": item.get("section") or "decisions",
                "text": text[:400],
                "because": str(item.get("because") or "")[:300],
                "created_at": moment.isoformat(),
            }
        )
        known.add(text)
        added += 1
    box["pending"] = pending[-20:]
    profile.suggestions = box
    session.add(
        AuditLog(
            user_id=user_id,
            actor="system",
            action="learning.distilled",
            subject_type="profile",
            subject_id=str(user_id),
            detail={"added": added, "evidence": {k: len(v) for k, v in evidence.items()}},
        )
    )
    await session.flush()
    return {"added": added, "pending": len(box["pending"])}


def resolve_suggestion(
    profile: Profile, suggestion_id: str, *, accept: bool
) -> dict[str, Any] | None:
    box = dict(profile.suggestions or {})
    pending = list(box.get("pending") or [])
    match = next((p for p in pending if p.get("id") == suggestion_id), None)
    if match is None:
        return None
    pending.remove(match)
    if accept:
        section = match.get("section") or "decisions"
        current = (getattr(profile, section, "") or "").rstrip()
        setattr(profile, section, f"{current}\n{match['text']}".strip()[:4000])
    else:
        box["dismissed"] = (list(box.get("dismissed") or []) + [match])[-50:]
    box["pending"] = pending
    profile.suggestions = box
    return match
