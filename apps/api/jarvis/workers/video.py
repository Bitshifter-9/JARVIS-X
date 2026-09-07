"""The video worker: renders YouTube drafts and dispatches approved uploads.

Two job kinds, one loop:

``youtube.generate``
    Runs the whole render pipeline, then *proposes* ``youtube.upload`` through the
    tool gateway. Uploading is an R2 external effect, so a human approves it first —
    on the Telegram card or the app's Approvals screen.

``youtube.upload`` / ``youtube.reply``
    Poll their action. Approved → dispatch, execute, verify with real provider
    evidence. Still waiting → back to pending with backoff. Rejected or expired →
    done, nothing published.

``youtube.analytics``
    Pulls the channel report; its result row doubles as the memory the script
    prompt reads its "past winners" from.

``youtube.comments``
    Sweeps comments on uploaded videos, drafts replies with the LLM, and proposes
    each as an R2 ``youtube.reply`` action — nothing posts without approval.

With ``JARVIS_YOUTUBE_DAILY_TOPIC`` set, the worker also arms one render, one
analytics refresh, and one comment sweep per day (idempotency-keyed by date).

Claims only ``youtube.*`` kinds, so it can run beside any future general worker
without stealing its jobs.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text

from jarvis.connectors.google.oauth import TokenStore
from jarvis.connectors.google.youtube import YouTubeConnector
from jarvis.connectors.telegram.client import TelegramClient
from jarvis.connectors.telegram.service import TelegramService
from jarvis.core.config import get_settings
from jarvis.core.errors import PolicyDenied
from jarvis.core.logging import get_logger
from jarvis.db.models.agent import Action, ActionStatus
from jarvis.db.queue import JobQueue
from jarvis.db.session import session_scope
from jarvis.services.evidence import EvidenceService
from jarvis.services.tool_gateway import ToolGateway
from jarvis.services.youtube import pipeline

log = get_logger(__name__)

KINDS = [
    "youtube.generate",
    "youtube.upload",
    "youtube.analytics",
    "youtube.comments",
    "youtube.reply",
]
# How long a rendered video waits for its upload approval before it is abandoned.
UPLOAD_WINDOW = timedelta(hours=6)
MAX_REPLIES_PER_SWEEP = 5


class Waiting:
    """Not done, not failed — parked with a reason until conditions change."""

    def __init__(self, reason: str = "awaiting upload approval") -> None:
        self.reason = reason


async def propose_upload(session, user_id: uuid.UUID, result: dict, *, rationale: str):  # noqa: ANN001, ANN201
    """Propose the R2 upload for a finished render and arm the job that executes it
    once approved. Also used to re-propose after an approval window lapses."""
    proposal = await ToolGateway(session).propose(
        user_id,
        tool="youtube.upload",
        args={
            "file_path": result["video_path"],
            "title": result["title"],
            "description": result.get("description", ""),
            "tags": result.get("tags", []),
            "privacy": get_settings().youtube_privacy,
        },
        rationale=rationale,
        ttl=UPLOAD_WINDOW,
        approval_ttl=UPLOAD_WINDOW,
    )
    if proposal.needs_approval:
        await _send_approval_card(session, proposal)

    await JobQueue(session).enqueue(
        "youtube.upload",
        {"action_id": str(proposal.action.id), "user_id": str(user_id)},
        user_id=user_id,
        delay_seconds=10,
        max_attempts=999,  # the retries are approval polling, not failures
    )
    return proposal


async def handle_generate(session, job) -> dict:  # noqa: ANN001
    user_id = uuid.UUID(job.payload["user_id"])
    topic = job.payload["topic"]

    # A render can outlive the 300s job lease; extend it up front so the reaper does
    # not hand a half-rendered job to a second worker.
    await session.execute(
        text("UPDATE jobs SET visible_at = clock_timestamp() + interval '1 hour' "
             "WHERE id = :id"),
        {"id": job.id},
    )

    result = await pipeline.generate(session, user_id=user_id, topic=topic, job_id=job.id)
    proposal = await propose_upload(
        session, user_id, result, rationale=f"YouTube pipeline: {topic}"
    )
    return {**result, "action_id": str(proposal.action.id)}


async def _dispatch_approved(session, job, execute):  # noqa: ANN001, ANN201
    """The shared approval-polling skeleton for youtube.upload and youtube.reply.

    ``execute(connector, account_id, action)`` performs the effect and returns
    ``(provider_object_id, result_dict)``.
    """
    action = await session.get(
        Action, uuid.UUID(job.payload["action_id"]), populate_existing=True
    )
    if action is None:
        return {"skipped": "action missing"}

    if action.status == ActionStatus.AWAITING_APPROVAL.value:
        if action.expires_at <= datetime.now(UTC):
            return {"skipped": "approval window expired — use Publish on the render to retry"}
        return Waiting()

    if action.status != ActionStatus.APPROVED.value:
        # Includes DISPATCHED after a mid-flight crash: these effects are not
        # idempotent, so they are never re-fired blindly.
        return {"skipped": f"action is {action.status}; nothing was published"}

    # The account check comes BEFORE dispatch: authorize_dispatch stamps the action
    # DISPATCHED, and a wait after that point could never be resumed.
    store = TokenStore(session)
    account = await store.find(action.user_id, "gmail")
    if account is None:
        if action.expires_at <= datetime.now(UTC):
            return {"skipped": "no Google account connected before the window closed"}
        return Waiting("approved — waiting for a Google account (Settings → Connect Google)")

    try:
        action = await ToolGateway(session).authorize_dispatch(action.id)
    except PolicyDenied as exc:
        return {"skipped": f"dispatch refused: {exc}"}

    try:
        provider_id, result = await execute(YouTubeConnector(store), account.id, action)
    except Exception:
        # The call raised, so nothing was published — put the action back to
        # APPROVED and let the job retry cleanly. The DISPATCHED-skip above only
        # protects the no-exception crash window (effect unknown).
        action.status = ActionStatus.APPROVED.value
        raise
    outcome = await EvidenceService(session).verify(
        action, {"provider_object_id": provider_id}, uri=result.get("url")
    )
    action.status = (
        ActionStatus.SUCCEEDED.value if outcome.success else ActionStatus.FAILED.value
    )
    return {**result, "verified": outcome.success}


async def handle_upload(session, job):  # noqa: ANN001, ANN201
    async def execute(yt, account_id, action):  # noqa: ANN001
        video_id = await yt.upload(account_id, **action.args)
        return video_id, {"video_id": video_id, "url": f"https://youtu.be/{video_id}"}

    return await _dispatch_approved(session, job, execute)


async def handle_reply(session, job):  # noqa: ANN001, ANN201
    async def execute(yt, account_id, action):  # noqa: ANN001
        reply_id = await yt.reply_comment(account_id, **action.args)
        return reply_id, {"reply_id": reply_id, "video_id": action.args.get("video_id")}

    return await _dispatch_approved(session, job, execute)


async def handle_analytics(session, job) -> dict:  # noqa: ANN001
    store = TokenStore(session)
    account = await store.find(uuid.UUID(job.payload["user_id"]), "gmail")
    if account is None:
        return {"skipped": "no Google account connected"}
    return await YouTubeConnector(store).channel_report(account.id)


async def handle_comments(session, job) -> dict:  # noqa: ANN001
    """Draft replies to unanswered comments; each becomes an R2 approval."""
    from jarvis.llm.router import LLMRouter
    from jarvis.llm.types import Message
    from jarvis.services.event.service import EventService

    user_id = uuid.UUID(job.payload["user_id"])
    store = TokenStore(session)
    account = await store.find(user_id, "gmail")
    if account is None:
        return {"skipped": "no Google account connected"}

    video_ids = [
        r
        for (r,) in (
            await session.execute(
                text("""
                    SELECT result->>'video_id' FROM jobs
                    WHERE user_id = :u AND kind = 'youtube.upload'
                      AND status = 'succeeded' AND result ? 'video_id'
                    ORDER BY id DESC LIMIT 5
                """),
                {"u": str(user_id)},
            )
        ).all()
    ]
    if not video_ids:
        return {"skipped": "no uploaded videos yet"}

    yt = YouTubeConnector(store)
    router = LLMRouter(session)
    gateway = ToolGateway(session)
    queue = JobQueue(session)
    proposed = 0

    for video_id in video_ids:
        for comment in await yt.list_comments(account.id, video_id):
            if proposed >= MAX_REPLIES_PER_SWEEP:
                break
            if comment["reply_count"] > 0:
                continue
            # A prior sweep already proposed a reply here — the job's idempotency key
            # records that; skip before creating a duplicate approval card.
            already = await session.scalar(
                text("SELECT 1 FROM jobs WHERE idempotency_key = :k"),
                {"k": f"yt-reply:{comment['parent_id']}"},
            )
            if already:
                continue
            response = await router.chat(
                [
                    Message(
                        "system",
                        "You are the owner of this YouTube channel. Write one short, "
                        "warm, helpful reply (max 2 sentences, no hashtags, no links).",
                    ),
                    Message("user", EventService.untrusted(comment["text"][:500])),
                ],
                user_id=user_id,
                max_tokens=120,
            )
            proposal = await gateway.propose(
                user_id,
                tool="youtube.reply",
                args={
                    "parent_id": comment["parent_id"],
                    "text": response.text.strip()[:1000],
                    "video_id": video_id,
                },
                rationale=f"Reply to {comment['author']}: {comment['text'][:120]}",
                ttl=UPLOAD_WINDOW,
                approval_ttl=UPLOAD_WINDOW,
                # The draft answers untrusted text but was authored by our LLM with a
                # fixed persona; the human approval below is the actual gate.
            )
            if proposal.needs_approval:
                await _send_approval_card(session, proposal)
            await queue.enqueue(
                "youtube.reply",
                {"action_id": str(proposal.action.id), "user_id": str(user_id)},
                user_id=user_id,
                delay_seconds=10,
                max_attempts=999,
                idempotency_key=f"yt-reply:{comment['parent_id']}",
            )
            proposed += 1

    return {"videos_checked": len(video_ids), "replies_proposed": proposed}


async def _send_approval_card(session, proposal) -> None:  # noqa: ANN001
    """Best-effort: the approval also shows in the app, so Telegram failing is not fatal."""
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_owner_chat_id):
        return
    try:
        await TelegramService(session, TelegramClient(s.telegram_bot_token)).send_approval_card(
            s.telegram_owner_chat_id, approval=proposal.approval, action=proposal.action
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("youtube_approval_card_failed", error=str(exc)[:200])


HANDLERS = {
    "youtube.generate": handle_generate,
    "youtube.upload": handle_upload,
    "youtube.reply": handle_reply,
    "youtube.analytics": handle_analytics,
    "youtube.comments": handle_comments,
}


async def arm_daily(session) -> None:  # noqa: ANN001
    """One render + analytics refresh + comment sweep per day, idempotent by date."""
    s = get_settings()
    if not s.youtube_daily_topic:
        return
    now = datetime.now(ZoneInfo(s.timezone))
    if now.hour < s.youtube_daily_hour:
        return
    # ponytail: single-tenant — the earliest user is the owner (uuid7 ids sort by time).
    owner = (
        await session.execute(text("SELECT id FROM users ORDER BY id LIMIT 1"))
    ).scalar()
    if owner is None:
        return
    today = now.date().isoformat()
    queue = JobQueue(session)
    await queue.enqueue(
        "youtube.generate",
        {"topic": s.youtube_daily_topic, "user_id": str(owner)},
        user_id=owner, idempotency_key=f"yt-daily:{today}", max_attempts=2,
    )
    await queue.enqueue(
        "youtube.analytics", {"user_id": str(owner)},
        user_id=owner, idempotency_key=f"yt-analytics:{today}",
    )
    await queue.enqueue(
        "youtube.comments", {"user_id": str(owner)},
        user_id=owner, idempotency_key=f"yt-comments:{today}",
    )


async def run_forever(*, poll_seconds: float = 5.0) -> None:
    worker_id = f"video-{uuid.uuid4().hex[:8]}"
    log.info("video_worker_started", worker_id=worker_id, kinds=KINDS)

    daily_armed_for: str | None = None
    while True:
        worked = False
        try:
            async with session_scope() as session:
                queue = JobQueue(session)
                await queue.reap_expired()

                today = datetime.now(ZoneInfo(get_settings().timezone)).date().isoformat()
                if daily_armed_for != today:
                    await arm_daily(session)
                    # enqueue is idempotent, but only mark armed once the hour has
                    # actually passed so pre-hour ticks keep checking.
                    if datetime.now(ZoneInfo(get_settings().timezone)).hour >= (
                        get_settings().youtube_daily_hour
                    ):
                        daily_armed_for = today

                for job in await queue.claim(worker_id, limit=1, kinds=KINDS):
                    worked = True
                    handler = HANDLERS[job.kind]
                    try:
                        outcome = await handler(session, job)
                    except Exception as exc:  # noqa: BLE001 — recorded on the job, loop survives
                        log.error(
                            "video_job_failed",
                            job_id=str(job.id), kind=job.kind, error=str(exc)[:300],
                        )
                        await queue.fail(job.id, f"{type(exc).__name__}: {exc}")
                    else:
                        if isinstance(outcome, Waiting):
                            await queue.fail(job.id, outcome.reason, retry=True)
                        else:
                            await queue.complete(job.id, outcome)
        except Exception as exc:  # noqa: BLE001 — a tick failing must not stop the loop
            log.error("video_worker_tick_failed", error=str(exc)[:300])
        if not worked:
            await asyncio.sleep(poll_seconds)


if __name__ == "__main__":
    from jarvis.core.logging import configure_logging

    configure_logging(level=get_settings().log_level)
    asyncio.run(run_forever())
