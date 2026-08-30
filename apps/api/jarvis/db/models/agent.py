"""Agent runs, proposed actions, approvals and evidence.

The safety spine (blueprint §4 and §9). Three properties the schema itself enforces:

* An action carries the **evidence it expects**, so "did it work?" has an answer that is
  not the tool's own opinion.
* An approval stores a **hash over the whole payload**, so an edited proposal is a
  different proposal.
* A suspended run is a **row**, not a waiting process — which is why the agent never
  stays alive waiting for a person.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.db.base import Base, Timestamps, UUIDPrimaryKey


class RunState(enum.StrEnum):
    """The nine states from blueprint §4."""

    INGEST = "ingest"
    CLASSIFY = "classify"
    CONTEXT = "context"
    PLAN = "plan"
    POLICY = "policy"
    EXECUTE = "execute"
    VERIFY = "verify"
    REFLECT = "reflect"
    COMMIT = "commit"


class RunStatus(enum.StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_USER = "awaiting_user"
    SUCCEEDED = "succeeded"
    STOPPED = "stopped"
    FAILED = "failed"


class Risk(enum.StrEnum):
    R0 = "R0"  # read-only
    R1 = "R1"  # reversible local
    R2 = "R2"  # external effect
    R3 = "R3"  # destructive / privileged
    R4 = "R4"  # prohibited


class ActionStatus(enum.StrEnum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    DENIED = "denied"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    SIMULATED = "simulated"


class Verdict(enum.StrEnum):
    VERIFIED = "verified"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class AgentRun(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "agent_runs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"))
    trigger: Mapped[str] = mapped_column(String(64), nullable=False, default="chat")

    state: Mapped[str] = mapped_column(String(16), nullable=False, default=RunState.INGEST.value)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=RunStatus.RUNNING.value)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replans: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Budgets travel with the run, so a resumed run cannot quietly restart its allowance.
    budget: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    simulate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Action(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "actions"
    __table_args__ = (Index("ix_actions_run_status", "run_id", "status"),)

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    args: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    risk: Mapped[str] = mapped_column(String(4), nullable=False)
    # At least one requirement, always. An action nobody can verify is not dispatchable.
    expected: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ActionStatus.PROPOSED.value
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    simulate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rationale: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB)
    correlation_id: Mapped[str | None] = mapped_column(String(32), index=True)


class Approval(UUIDPrimaryKey, Timestamps, Base):
    """A pending human decision.

    The client only ever sees ``id``. ``payload_hash`` binds the decision to the exact
    tool, args, user, device and expiry that were shown — editing any of them produces a
    different hash, and therefore a different proposal.
    """

    __tablename__ = "approvals"

    action_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("actions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payload_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    decision: Mapped[str | None] = mapped_column(String(16))  # approved | rejected
    decided_by: Mapped[str | None] = mapped_column(String(64))  # channel that decided
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # R3 additionally requires a local confirmation on the Mac itself.
    requires_local_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    local_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Evidence(UUIDPrimaryKey, Timestamps, Base):
    """Observed state after an action. Not a status code, and never model confidence."""

    __tablename__ = "evidence"

    action_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("actions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    expected: Mapped[dict | None] = mapped_column(JSONB)
    observed: Mapped[dict | None] = mapped_column(JSONB)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)

    uri: Mapped[str | None] = mapped_column(Text)
    digest: Mapped[str | None] = mapped_column(String(80))
    redaction: Mapped[dict | None] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
