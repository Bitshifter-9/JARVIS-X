"""The connector poller: the ``worker`` entrypoint's ingestion half.

Every ``gmail_poll_seconds`` it asks each connected account "what changed?", stores each
new object once, and ingests one event per object — which the agent worker then turns
into a sourced task. Nothing here calls a model, and nothing here decides anything:
provider content is stored as **untrusted data** and handed on.

Push-based providers (Slack, OpenClaw, Telegram) never appear here; their webhooks
already ingest. This loop exists for the ones that must be asked.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.connectors.base import SyncItem
from jarvis.core.config import get_settings
from jarvis.core.correlation import ensure_correlation_id
from jarvis.core.errors import Forbidden
from jarvis.core.logging import get_logger
from jarvis.core.overrides import apply_overrides
from jarvis.db.models.source import ConnectorCursor, SourceAccount
from jarvis.db.session import session_scope
from jarvis.services.event import EventService
from jarvis.services.event.envelope import EventEnvelope, EventSource, EventType, Trust

log = get_logger(__name__)

# Google's one consent grants several products; the row is stored as ``gmail`` and the
# scopes say what else it can read.
CLASSROOM_SCOPE = "classroom.coursework.me.readonly"


def _connectors_for(session: AsyncSession, account: SourceAccount) -> list[tuple[str, Any]]:
    from jarvis.connectors.google.oauth import TokenStore

    store = TokenStore(session)
    found: list[tuple[str, Any]] = []
    if account.provider == "gmail":
        from jarvis.connectors.google.gmail import GmailConnector

        found.append(("gmail", GmailConnector(store)))
        if any(CLASSROOM_SCOPE in s for s in account.scopes or []):
            from jarvis.connectors.google.classroom import ClassroomConnector

            found.append(("classroom", ClassroomConnector(store)))
    return found


async def _cursor(session: AsyncSession, account_id: uuid.UUID, kind: str) -> ConnectorCursor:
    row = await session.scalar(
        select(ConnectorCursor).where(
            ConnectorCursor.account_id == account_id, ConnectorCursor.kind == kind
        )
    )
    if row is None:
        row = ConnectorCursor(account_id=account_id, kind=kind, cursor=None)
        session.add(row)
        await session.flush()
    return row


async def ingest_item(
    session: AsyncSession, *, user_id: uuid.UUID, account_id: uuid.UUID | None, item: SyncItem
) -> bool:
    """Store one provider object and raise one event for it. Returns whether it was new."""
    events = EventService(session)
    await events.upsert_source_object(
        user_id=user_id,
        account_id=account_id,
        provider=item.provider,
        object_id=item.object_id,
        kind=item.kind,
        title=item.title,
        excerpt=(item.body or "")[:12_000],
        author=item.author,
        occurred_at=item.occurred_at,
        url=item.url,
        raw=item.raw or None,
    )
    payload: dict[str, Any] = {
        "kind": item.kind,
        "title": item.title,
        "author": item.author,
        "url": item.url,
    }
    # Coursework and assignments carry a structured due date: the agent worker will use
    # it directly and never consult the extractor.
    if item.kind in ("coursework", "assignment") and item.occurred_at is not None:
        payload["due_at"] = item.occurred_at.isoformat()
    result = await events.ingest(
        EventEnvelope(
            event_type=EventType.SOURCE_MESSAGE_CHANGED,
            occurred_at=item.occurred_at or datetime.now(UTC),
            tenant_id=user_id,
            source=EventSource(
                provider=item.provider, object_id=item.object_id, account_id=account_id
            ),
            correlation_id=ensure_correlation_id(),
            trust=Trust.UNTRUSTED,
            payload=payload,
        )
    )
    if not result.duplicate:
        from jarvis.services.routines import RoutineService

        await RoutineService(session).on_message(
            user_id, provider=item.provider, author=item.author, title=item.title, body=item.body
        )
    return not result.duplicate


async def sync_account(session: AsyncSession, account: SourceAccount) -> dict[str, int]:
    """Poll one account. What happened is written on the row — the Connections screen
    shows "scanned 3 minutes ago, 2 new" or the exact error, never a guess."""
    counts: dict[str, int] = {}
    seen: dict[str, int] = {}
    errors: list[str] = []
    for kind, connector in _connectors_for(session, account):
        cursor = await _cursor(session, account.id, kind)
        try:
            page = await connector.sync(account.id, cursor.cursor)
        except Forbidden as exc:
            # Not retried blindly: the user must reconnect. Visible on the Connections
            # screen, which is where they will look.
            account.status = "reauth_required"
            errors.append(f"{kind}: {str(exc)[:160]}")
            log.warning("connector_reauth_required", provider=kind, reason=str(exc)[:120])
            continue
        except Exception as exc:  # noqa: BLE001 — recorded on the row; the next kind still runs
            errors.append(f"{kind}: {type(exc).__name__}: {str(exc)[:160]}")
            log.warning("connector_sync_error", provider=kind, error=str(exc)[:200])
            continue
        new = 0
        for item in page.items:
            if await ingest_item(
                session, user_id=account.user_id, account_id=account.id, item=item
            ):
                new += 1
        cursor.cursor = page.cursor
        counts[kind] = new
        seen[kind] = len(page.items)
    now = datetime.now(UTC)
    account.last_synced_at = now
    account.last_sync_result = {"at": now.isoformat(), "new": counts, "seen": seen}
    account.last_error = "; ".join(errors) if errors else None
    await session.flush()
    return counts


async def sync_canvas(session: AsyncSession) -> int:
    """Canvas is configured per deployment, not per account (single-tenant demo)."""
    from sqlalchemy import text

    from jarvis.connectors.canvas import CanvasConnector

    s = get_settings()
    if not (s.canvas_base_url and s.canvas_api_token):
        return 0
    owner = (await session.execute(text("SELECT id FROM users ORDER BY id LIMIT 1"))).scalar()
    if owner is None:
        return 0
    page = await CanvasConnector(s.canvas_base_url, s.canvas_api_token).sync(owner, None)
    return sum(
        [await ingest_item(session, user_id=owner, account_id=None, item=i) for i in page.items]
    )


async def tick(session: AsyncSession) -> dict[str, Any]:
    accounts = list(
        (
            await session.scalars(
                select(SourceAccount).where(
                    SourceAccount.revoked_at.is_(None), SourceAccount.status == "active"
                )
            )
        ).all()
    )
    report: dict[str, Any] = {}
    for account in accounts:
        try:
            report[str(account.id)] = await sync_account(session, account)
        except Exception as exc:  # noqa: BLE001 — one account failing must not stop the rest
            log.error("connector_sync_failed", provider=account.provider, error=str(exc)[:300])
    try:
        report["canvas"] = await sync_canvas(session)
    except Exception as exc:  # noqa: BLE001
        log.error("connector_sync_failed", provider="canvas", error=str(exc)[:300])
    try:
        report["slack"] = await scan_slack_all(session)
    except Exception as exc:  # noqa: BLE001
        log.error("connector_sync_failed", provider="slack", error=str(exc)[:300])
    try:
        report["insights"] = await derive_insights_all(session, accounts)
    except Exception as exc:  # noqa: BLE001
        log.error("connector_sync_failed", provider="insights", error=str(exc)[:300])
    try:
        report["commitments"] = await scan_commitments_all(session, accounts)
    except Exception as exc:  # noqa: BLE001
        log.error("connector_sync_failed", provider="commitments", error=str(exc)[:300])
    return report


async def scan_commitments_all(session: AsyncSession, accounts: list[SourceAccount]) -> int:
    from jarvis.services.commitment import scan_commitments

    total = 0
    for uid in {a.user_id for a in accounts}:
        total += await scan_commitments(session, uid)
    return total


async def derive_insights_all(session: AsyncSession, accounts: list[SourceAccount]) -> int:
    """Read new mail into labels/spending/travel for every user with a mail account."""
    from jarvis.services.insights import derive_insights

    user_ids = {a.user_id for a in accounts if a.provider in ("gmail",)}
    total = 0
    for uid in user_ids:
        total += await derive_insights(session, uid)
    return total


async def scan_slack_all(session: AsyncSession) -> dict[str, int]:
    """The Slack reconciliation scan, if configured (deployment-level bot token)."""
    s = get_settings()
    if not (s.slack_scan_enabled and s.slack_bot_token):
        return {"channels": 0, "new": 0}
    from jarvis.connectors.slack.client import SlackClient
    from jarvis.connectors.slack.service import scan_slack

    return await scan_slack(session, SlackClient(s.slack_bot_token))


async def run_forever(*, poll_seconds: float | None = None) -> None:
    from jarvis.workers.pulse import pulse

    interval = poll_seconds or float(get_settings().gmail_poll_seconds)
    log.info("connector_worker_started", poll_seconds=interval)
    while True:
        try:
            async with session_scope() as session:
                await apply_overrides(session)
                report = await tick(session)
                await pulse(session, "connector", report=report, poll_seconds=interval)
            if any(v for v in report.values() if v):
                log.info("connector_tick", report=report)
        except Exception as exc:  # noqa: BLE001
            log.error("connector_tick_failed", error=str(exc)[:300])
        await asyncio.sleep(interval)


if __name__ == "__main__":
    from jarvis.core.logging import configure_logging

    configure_logging(level=get_settings().log_level)
    asyncio.run(run_forever())
