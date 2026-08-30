"""The job queue table — our replacement for SQS (PLAN.md §5).

One table provides what SQS gives: at-least-once delivery, a visibility timeout, bounded
retries with backoff, and a dead-letter destination. ``FOR UPDATE SKIP LOCKED`` supplies
the concurrency.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.db.base import Base, Timestamps, UUIDPrimaryKey


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"          # retryable; will return to pending after backoff
    DEAD_LETTERED = "dead_lettered"
    CANCELLED = "cancelled"    # e.g. by the kill switch


class Job(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # The claim query's access path: status + visible_at, ordered by priority.
        Index(
            "ix_jobs_claimable",
            "status",
            "visible_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_jobs_kind_status", "kind", "status"),
    )

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # NULL means a system-scoped job (reconciliation, health sweeps). User-scoped work
    # always carries the tenant, and every user-facing query filters on it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JobStatus.PENDING.value)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Doubles as the scheduled-start time and, once claimed, as the lease expiry.
    visible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_error: Mapped[str | None] = mapped_column(Text)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Enforces exactly-once *enqueue* for a given logical event. The blueprint's
    # at-least-once guard, delegated to a unique index rather than to application code.
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    correlation_id: Mapped[str | None] = mapped_column(String(32), index=True)
    result: Mapped[dict | None] = mapped_column(JSONB)
