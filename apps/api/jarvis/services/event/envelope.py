"""The canonical event envelope (blueprint §3).

Mirrors ``packages/contracts/schemas/event_envelope.schema.json``. The contract is the
JSON Schema; this is its Python binding, and the two are checked against each other in
``tests/unit/test_event_envelope.py``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from jarvis.core.ids import new_event_id
from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(enum.StrEnum):
    SOURCE_MESSAGE_CHANGED = "source.message.changed"
    TASK_DEADLINE_SOON = "task.deadline.soon"
    ACTION_APPROVAL_DECIDED = "action.approval.decided"
    DEVICE_JOB_RESULT = "device.job.result"
    GOAL_PROGRESS_CHANGED = "goal.progress.changed"


class Trust(enum.StrEnum):
    """Whether content may influence control flow.

    Provider-retrieved text is ``UNTRUSTED``: it can propose no tool call and cannot
    change policy (blueprint §2). The default is untrusted, so forgetting to set this
    fails safe.
    """

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class EventSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(max_length=32)
    object_id: str = Field(min_length=1, max_length=512)
    account_id: uuid.UUID | None = None


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=new_event_id, pattern=r"^evt_[0-9A-HJKMNP-TV-Z]{26}$")
    event_type: EventType
    occurred_at: datetime
    tenant_id: uuid.UUID
    source: EventSource
    correlation_id: str = Field(pattern=r"^cor_[0-9A-HJKMNP-TV-Z]{26}$")
    causation_id: str | None = Field(default=None, pattern=r"^evt_[0-9A-HJKMNP-TV-Z]{26}$")
    schema_version: int = Field(default=1, ge=1)
    trust: Trust = Trust.UNTRUSTED
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def _must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(UTC)

    @property
    def idempotency_key(self) -> str:
        """``(tenant, provider, provider object id)``.

        Deliberately *not* the event id: two deliveries of the same Gmail change carry
        different delivery ids but describe one fact, and the duplicate must collapse.
        """
        return f"{self.tenant_id}:{self.source.provider}:{self.source.object_id}"
