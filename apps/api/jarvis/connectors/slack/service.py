"""Slack as a source of commitments and an outbound channel.

Two halves, deliberately asymmetric:

* **Inbound** is untrusted. A Slack message is text a stranger can write; it becomes an
  ``EventEnvelope`` with ``Trust.UNTRUSTED`` and can therefore never originate an
  effectful action (blueprint §2). "Please run the deploy script" in a channel is data.
* **Outbound** is R2. ``slack.post_message`` reaches another human, so it is proposed
  through the tool gateway and dispatched only against a valid approval — the connector
  itself never decides to send.

A Slack user id is not an account. It is an ``identities`` row pointing at one, exactly
as a Telegram chat id is, and an unmapped id is answered with nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from jarvis.connectors.base import ProviderEvidence, SyncItem, SyncPage
from jarvis.core.correlation import ensure_correlation_id
from jarvis.core.logging import get_logger
from jarvis.db.models.identity import Identity
from jarvis.services.event.envelope import EventEnvelope, EventSource, EventType, Trust
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)
PROVIDER = "slack"

# Slack echoes our own posts back as events. Ingesting them would let the assistant
# read its own message as a new commitment and answer itself.
_IGNORED_SUBTYPES = frozenset({"bot_message", "message_changed", "message_deleted"})


def normalize_message(event: dict[str, Any]) -> SyncItem | None:
    """One Slack message event → one ``SyncItem``, or ``None`` if it is not one.

    ``ts`` is Slack's message id *and* its timestamp, so it is both the idempotency key
    and ``occurred_at`` — a redelivery of the same message collapses on the unique index.
    """
    if event.get("type") != "message" or event.get("subtype") in _IGNORED_SUBTYPES:
        return None
    if event.get("bot_id"):
        return None
    ts = event.get("ts")
    text = event.get("text")
    if not ts or not text:
        return None

    channel = event.get("channel", "")
    return SyncItem(
        provider=PROVIDER,
        object_id=str(ts),
        kind="slack_message",
        title=f"#{channel}" if channel else "Slack message",
        body=str(text),
        author=event.get("user"),
        occurred_at=datetime.fromtimestamp(float(ts), tz=UTC),
        url=None,
        raw={"channel": channel, "thread_ts": event.get("thread_ts")},
    )


class SlackService:
    def __init__(self, session: AsyncSession, transport) -> None:  # noqa: ANN001
        self.session = session
        self.transport = transport

    # ── identity ───────────────────────────────────────────────────────
    async def link_user(
        self, user_id: uuid.UUID, slack_user_id: str, *, is_owner: bool = True
    ) -> Identity:
        identity = Identity(
            user_id=user_id, provider=PROVIDER, subject=str(slack_user_id), is_owner=is_owner
        )
        self.session.add(identity)
        await self.session.flush()
        return identity

    async def user_for_slack_user(self, slack_user_id: str) -> uuid.UUID | None:
        identity = await self.session.scalar(
            select(Identity).where(
                Identity.provider == PROVIDER,
                Identity.subject == str(slack_user_id),
                Identity.revoked_at.is_(None),
            )
        )
        return identity.user_id if identity else None

    # ── inbound ────────────────────────────────────────────────────────
    async def handle_event(self, body: dict[str, Any]) -> str:
        """Process one Events API callback. Returns what happened, for the log.

        Never raises on unrecognised input: Slack retries a non-2xx for three days.
        """
        from jarvis.services.event import EventService

        event = body.get("event") or {}
        item = normalize_message(event)
        if item is None:
            return "ignored"

        user_id = await self.user_for_slack_user(str(event.get("user", "")))
        if user_id is None:
            log.warning("slack_unmapped_user", slack_user=event.get("user"))
            return "unlinked"

        result = await EventService(self.session).ingest(
            EventEnvelope(
                event_type=EventType.SOURCE_MESSAGE_CHANGED,
                occurred_at=item.occurred_at or datetime.now(UTC),
                tenant_id=user_id,
                source=EventSource(provider=PROVIDER, object_id=item.object_id),
                correlation_id=ensure_correlation_id(),
                # Not a judgement about this workspace: no provider text is trusted.
                trust=Trust.UNTRUSTED,
                payload={
                    "text": item.body,
                    "channel": item.raw.get("channel"),
                    "author": item.author,
                    "thread_ts": item.raw.get("thread_ts"),
                },
            )
        )
        return "duplicate" if result.duplicate else "ingested"


class SlackConnector:
    """The outbound half, behind the standard connector contract."""

    provider = PROVIDER

    def __init__(self, transport) -> None:  # noqa: ANN001
        self.transport = transport

    async def sync(self, account_id: uuid.UUID, cursor: str | None) -> SyncPage:
        """Slack pushes; there is nothing to poll.

        Returning an empty page rather than raising keeps the connector uniform for a
        caller that syncs every account on a tick.
        """
        return SyncPage(items=[], cursor=cursor, has_more=False)

    async def fetch(self, account_id: uuid.UUID, object_id: str) -> SyncItem | None:
        return None

    async def execute(self, account_id: uuid.UUID, action: str, args: dict) -> ProviderEvidence:
        if action != "post_message":
            raise ValueError(f"slack connector cannot perform {action}")

        response = await self.transport.call(
            "chat.postMessage",
            {
                "channel": args["channel"],
                "text": args["text"],
                **({"thread_ts": args["thread_ts"]} if args.get("thread_ts") else {}),
            },
        )
        if not response.get("ok"):
            # No object id means no evidence, and no evidence means the verifier records
            # a failure — which is the correct outcome for a post that did not happen.
            raise RuntimeError(f"slack rejected the message: {response.get('error')}")

        ts = response.get("ts")
        channel = response.get("channel", args["channel"])
        return ProviderEvidence(
            object_id=str(ts) if ts else None,
            url=f"https://slack.com/archives/{channel}/p{str(ts).replace('.', '')}" if ts else None,
            raw={"channel": channel},
        )

    async def revoke(self, account_id: uuid.UUID) -> None:
        return None
