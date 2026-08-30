"""Events and provider-sourced objects.

The idempotency story lives here. Providers deliver at-least-once, so the same Gmail
history id will arrive twice; ``events.idempotency_key`` is a unique index and the
database, not application code, is what collapses the replay.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.db.base import Base, Timestamps, UUIDPrimaryKey


class SourceAccount(UUIDPrimaryKey, Timestamps, Base):
    """One connected provider account, with the scopes the user actually granted."""

    __tablename__ = "source_accounts"
    __table_args__ = (
        Index("uq_source_accounts_user_provider_ext", "user_id", "provider", "external_id",
              unique=True),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    # Read and write scopes are separate on purpose: outbound permission is requested
    # only when the user enables the feature that needs it (blueprint §17).
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Access and refresh tokens. Never returned to a client, never passed to a model,
    # redacted in logs.
    credentials: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectorCursor(UUIDPrimaryKey, Timestamps, Base):
    """Where each connector left off.

    Reconciliation runs even when webhooks look healthy, because "looks healthy" is not
    the same as "delivered everything".
    """

    __tablename__ = "connector_cursors"
    __table_args__ = (
        Index("uq_connector_cursors_account_kind", "account_id", "kind", unique=True),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_accounts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text)
    subscription_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceObject(UUIDPrimaryKey, Timestamps, Base):
    """A message, assignment or calendar entry we were permitted to read.

    Content here is **untrusted data** (blueprint §2). It can propose no tool call and
    cannot change policy; it exists so a task can cite where its deadline came from.
    """

    __tablename__ = "source_objects"
    __table_args__ = (
        Index("uq_source_objects_provider_account_object", "provider", "account_id", "object_id",
              unique=True),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_accounts.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(320))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(UUIDPrimaryKey, Timestamps, Base):
    """The canonical envelope, persisted (blueprint §3)."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_type_created", "event_type", "created_at"),)

    event_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(512))
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_accounts.id", ondelete="SET NULL")
    )

    correlation_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    causation_id: Mapped[str | None] = mapped_column(String(32))
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Provider-retrieved content defaults to untrusted, and must be explicitly promoted.
    trust: Mapped[str] = mapped_column(String(16), nullable=False, default="untrusted")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # (user, provider, provider_event_id). The replay guard, enforced by the index.
    idempotency_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
