"""Scheduling, devices, notifications, memory, the knowledge graph and the audit log."""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.db.base import Base, Timestamps, UUIDPrimaryKey

# 384 dimensions: all-MiniLM-L6-v2 runs on the VPS CPU. A quarter the storage of a
# 1536-d API embedding, no rate limit, and the corpus never leaves our machine.
EMBEDDING_DIM = 384


class Schedule(UUIDPrimaryKey, Timestamps, Base):
    """A one-shot future alert. Replaces EventBridge Scheduler (PLAN.md §5).

    ``task_version`` is the whole trick: when it no longer matches the task's current
    version, the task moved and this fire is stale, so it exits without alerting.
    """

    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedules_due", "fire_at", postgresql_where=text("status = 'pending'")),
        Index("ix_schedules_task", "task_id", "task_version"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    task_version: Mapped[int | None] = mapped_column(Integer)

    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # T-24h | T-2h | T-1h | T-15m
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_reason: Mapped[str | None] = mapped_column(String(64))


class Device(UUIDPrimaryKey, Timestamps, Base):
    """A paired Mac. Holds a public key; the private half never leaves its Keychain."""

    __tablename__ = "devices"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(String(24), nullable=False, default="macos")
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Enforced again at the helper. Two independent checks, because one can be bypassed.
    allowed_bundle_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    capabilities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class DeviceConnection(UUIDPrimaryKey, Timestamps, Base):
    """One WebSocket session. The Mac dials out; nothing ever dials in."""

    __tablename__ = "device_connections"

    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationEndpoint(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "notification_endpoints"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(24), nullable=False)  # fcm|telegram|whatsapp|call
    address: Mapped[str] = mapped_column(String(512), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Escalation order. Cheapest and least intrusive first.
    escalation_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quiet_hours: Mapped[str | None] = mapped_column(String(32))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StandingPermission(UUIDPrimaryKey, Timestamps, Base):
    """A pre-granted allowance, so routine work does not ask every single time."""

    __tablename__ = "standing_permissions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    max_risk: Mapped[str] = mapped_column(String(4), nullable=False, default="R1")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Memory(UUIDPrimaryKey, Timestamps, Base):
    """Semantic and episodic memory, in the same database as goals and tasks.

    Co-location is the point: one query can filter by tenant and scope in SQL *before*
    the vector search runs, which keeps retrieval both cheap and tenant-safe.
    """

    __tablename__ = "memories"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # episodic|semantic|source
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)

    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A correction supersedes rather than overwrites, so the old belief stays auditable.
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memories.id", ondelete="SET NULL")
    )


class Entity(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "entities"
    __table_args__ = (Index("uq_entities_user_kind_name", "user_id", "kind", "name", unique=True),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class EntityAlias(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "entity_aliases"

    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(300), nullable=False)


class Relation(UUIDPrimaryKey, Timestamps, Base):
    """A graph edge. Provenance is required, not optional: every edge must answer
    "why do you believe this?" with a source."""

    __tablename__ = "relations"
    __table_args__ = (Index("ix_relations_subject_predicate", "subject_id", "predicate"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    predicate: Mapped[str] = mapped_column(String(48), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(UUIDPrimaryKey, Timestamps, Base):
    """Append-only. A kill switch never deletes evidence — it writes here instead."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_correlation", "correlation_id", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(48))
    subject_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(32))


class Artifact(UUIDPrimaryKey, Timestamps, Base):
    """Bytes a device produced on request: a screenshot, a file the user asked for.

    Stored on the server the moment they arrive and referenced by id everywhere else, so
    a device cannot claim an upload that did not happen — ``artifact_uploaded`` evidence
    is *this* row, minted here. The path is never exposed; ``GET /v1/artifacts/{id}``
    streams it to the owner and nobody else.
    """

    __tablename__ = "artifacts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("actions.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # screenshot|file|capture
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    delivered_to: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class SettingOverride(Timestamps, Base):
    """A setting the owner saved from the app. Overlaid on every process's environment."""

    __tablename__ = "settings_overrides"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class WorkerHeartbeat(Timestamps, Base):
    """Each background loop writes a row on every tick, so "is the worker alive" is a
    query rather than a guess."""

    __tablename__ = "worker_heartbeats"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_tick_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Profile(Timestamps, Base):
    """Who the user is, in their words — the part of the second brain they wrote.

    Everything here is shown to the model on every turn: it is the standing context that
    makes an answer *theirs*. Facts learned along the way go to ``memories``; this is the
    frame those facts sit in.
    """

    __tablename__ = "profiles"
    # Timestamps are server defaults and ``updated_at`` changes on every UPDATE; fetch
    # them with RETURNING so the async session never has to lazy-load them later.
    __mapper_args__ = {"eager_defaults": True}

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    about: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priorities: Mapped[str] = mapped_column(Text, nullable=False, default="")
    people: Mapped[str] = mapped_column(Text, nullable=False, default="")
    style: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decisions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    learned_style: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # User-defined personas: key → {name, description, instructions} (PLAN.md 10.3.2).
    personas: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # The nightly learning loop's proposals: {"pending": [...], "dismissed": [...]} (10.6.5).
    suggestions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # The shared clipboard, synced across devices: {text, updated_at, device} (#26).
    clipboard: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ChatFeedback(UUIDPrimaryKey, Timestamps, Base):
    """A thumbs up or down on a reply, with the reason when given. A reason becomes a
    memory the model sees next time — that is how correction becomes training."""

    __tablename__ = "chat_feedback"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL")
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)  # +1 | -1
    note: Mapped[str | None] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)


class Routine(UUIDPrimaryKey, Timestamps, Base):
    """A standing instruction with a clock or a trigger — "every morning, brief me",
    "when my professor mails, summarise it". Each firing is an ordinary ``agent.run`` job
    with the routine's prompt as trusted input; the message that triggered it is not."""

    __tablename__ = "routines"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(32))  # a built-in's key, else NULL
    cron: Mapped[str | None] = mapped_column(String(64))
    # {"provider": "gmail", "from": "prof", "subject": "assignment"} — substrings, any may be absent
    trigger: Mapped[dict | None] = mapped_column(JSONB)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="app")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result: Mapped[str | None] = mapped_column(Text)


class ActivitySample(UUIDPrimaryKey, Timestamps, Base):
    """What was in front of the owner, sampled: app and window title, never pixels
    (PLAN.md 10.6.3). Opt-in on each device, kept 30 days, owner-scoped."""

    __tablename__ = "activity_samples"
    __table_args__ = (Index("ix_activity_samples_user_at", "user_id", "at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )
    platform: Mapped[str] = mapped_column(String(16), nullable=False, default="macos")
    app: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
