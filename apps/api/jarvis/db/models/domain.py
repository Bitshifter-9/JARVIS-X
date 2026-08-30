"""Goals, tasks, dependencies, work sessions and predictions — the goal engine's tables.

This is the part of the schema the differentiating feature runs on (PLAN.md §8): the DAG
that yields a critical path, the work sessions that calibrate estimates against reality,
and the predictions that say when the current plan will miss.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.db.base import Base, Timestamps, UUIDPrimaryKey


class Goal(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "goals"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    outcome: Mapped[str | None] = mapped_column(Text)
    # A confirmed UTC instant plus its IANA zone. Reminders are computed from the pair,
    # never from the text a deadline was read out of (blueprint §7).
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str | None] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    success_metric: Mapped[dict | None] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Task(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_user_status_due", "user_id", "status", "due_at"),
        CheckConstraint(
            "estimate_minutes IS NULL OR estimate_minutes >= 0", name="estimate_nonneg"
        ),
        CheckConstraint(
            "remaining_minutes IS NULL OR remaining_minutes >= 0", name="remaining_nonneg"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")

    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str | None] = mapped_column(String(64))
    estimate_minutes: Mapped[int | None] = mapped_column(Integer)
    remaining_minutes: Mapped[int | None] = mapped_column(Integer)
    earliest_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)

    # Optional work is what a recovery plan proposes cutting first.
    is_optional: Mapped[bool] = mapped_column(nullable=False, default=False)

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_objects.id", ondelete="SET NULL")
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    # The exact source substring the deadline was read from. This is what makes
    # "why do you believe this?" answerable.
    evidence_span: Mapped[str | None] = mapped_column(Text)

    # Incremented on every update. A stale schedule fire reads the current version,
    # sees a mismatch, and exits without alerting (blueprint §7).
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskDependency(Timestamps, Base):
    """``task_id`` cannot start until ``depends_on`` is done."""

    __tablename__ = "task_dependencies"

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    __table_args__ = (
        # A task depending on itself is a cycle of length one, and the cheapest to reject.
        CheckConstraint("task_id <> depends_on", name="no_self_dependency"),
    )


class WorkSession(UUIDPrimaryKey, Timestamps, Base):
    """Actual time spent, which is how estimates get calibrated against reality."""

    __tablename__ = "work_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    interruptions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")


class GoalPrediction(UUIDPrimaryKey, Timestamps, Base):
    """One computed forecast, kept so the timeline can show how the outlook moved."""

    __tablename__ = "goal_predictions"
    __table_args__ = (Index("ix_goal_predictions_goal_created", "goal_id", "created_at"),)

    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    available_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    p50_remaining_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    p80_remaining_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    finish_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    calibration_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    critical_path: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    options: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # The sentence a human reads. Generated from these numbers, never hardcoded.
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
