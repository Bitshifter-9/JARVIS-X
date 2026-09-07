"""Internal ingress for isolated channel adapters (blueprint §18, PLAN.md phase 5.6).

OpenClaw runs as a **separate container with no database credentials**. It holds one
secret, and that secret buys exactly one right: to hand us an untrusted observation.
Everything it says arrives as ``Trust.UNTRUSTED``, so no OpenClaw message can originate
an effectful action — it can only produce an action *card* a human then approves.

That isolation is the whole point of the adapter pattern: a third-party channel with a
large surface never gets a connection string, and compromising it yields the ability to
say things, not the ability to do things.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from jarvis.api.deps import SessionDep
from jarvis.core.config import get_settings
from jarvis.core.correlation import ensure_correlation_id
from jarvis.core.errors import Forbidden
from jarvis.core.logging import get_logger
from jarvis.core.security import tokens_equal
from jarvis.db.models.identity import Identity
from jarvis.services.event import EventService
from jarvis.services.event.envelope import EventEnvelope, EventSource, EventType, Trust

log = get_logger(__name__)
router = APIRouter(prefix="/internal/connectors", tags=["internal"])

PROVIDER = "openclaw"


class OpenClawEvent(BaseModel):
    """What the adapter is allowed to say. Anything else is rejected by the schema."""

    subject: str = Field(min_length=1, max_length=128, description="The adapter's user handle")
    object_id: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=8000)
    occurred_at: datetime | None = None


def _authorize(presented: str | None) -> None:
    """Constant-time shared-secret check.

    An unset secret denies rather than allows: an internal route that is open when
    misconfigured is the failure mode this check exists to prevent.
    """
    expected = get_settings().openclaw_shared_secret
    if not expected or not presented or not tokens_equal(presented, expected):
        log.warning("openclaw_unauthorized")
        raise Forbidden("The OpenClaw adapter is not authorized")


@router.post("/openclaw/events", status_code=status.HTTP_202_ACCEPTED)
async def openclaw_event(
    body: OpenClawEvent,
    session: SessionDep,
    secret: Annotated[str | None, Header(alias="X-OpenClaw-Secret")] = None,
) -> dict[str, Any]:
    _authorize(secret)

    identity = await session.scalar(
        select(Identity).where(
            Identity.provider == PROVIDER,
            Identity.subject == body.subject,
            Identity.revoked_at.is_(None),
        )
    )
    if identity is None:
        # Not an error: an adapter serving many handles will see handles nobody linked.
        log.info("openclaw_unmapped_subject")
        return {"accepted": False, "reason": "unlinked subject"}

    result = await EventService(session).ingest(
        EventEnvelope(
            event_type=EventType.SOURCE_MESSAGE_CHANGED,
            occurred_at=body.occurred_at or datetime.now(UTC),
            tenant_id=identity.user_id,
            source=EventSource(provider=PROVIDER, object_id=body.object_id),
            correlation_id=ensure_correlation_id(),
            trust=Trust.UNTRUSTED,
            payload={"text": body.text, "subject": body.subject},
        )
    )
    return {"accepted": True, "duplicate": result.duplicate}


@router.get("/openclaw/health")
async def openclaw_health(
    secret: Annotated[str | None, Header(alias="X-OpenClaw-Secret")] = None,
) -> Response:
    """Lets the adapter check its own credential without posting an event."""
    _authorize(secret)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
