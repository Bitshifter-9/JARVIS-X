"""The signed job envelope exchanged with a paired Mac (blueprint §12).

Every job carries ``job_id``, ``action``, ``args``, ``nonce``, ``issued_at``,
``expires_at``, ``policy_version`` and a server signature. The helper validates all of
them — signature, expiry, nonce replay **and its own local allowlist** — before touching
anything.

The signature covers a canonical serialization, so two structurally identical envelopes
sign identically and a re-encoded copy cannot be passed off as a different job.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from jarvis.core.security import canonical_json


class MessageType(enum.StrEnum):
    # server → device
    JOB_DISPATCH = "job.dispatch"
    JOB_CANCEL = "job.cancel"
    SERVER_HELLO = "server.hello"
    # device → server
    DEVICE_HELLO = "device.hello"
    JOB_ACK = "job.ack"
    JOB_PROGRESS = "job.progress"
    JOB_RESULT = "job.result"
    DEVICE_HEARTBEAT = "device.heartbeat"
    DEVICE_PERMISSION_CHANGED = "device.permission.changed"


class RejectReason(enum.StrEnum):
    BAD_SIGNATURE = "bad_signature"
    EXPIRED = "expired"
    REPLAYED_NONCE = "replayed_nonce"
    NOT_ALLOWLISTED = "not_allowlisted"
    UNKNOWN_ACTION = "unknown_action"
    STOPPED = "stopped"


@dataclass(frozen=True)
class JobEnvelope:
    """A dispatchable unit of work, signed by the server."""

    job_id: str
    action: str
    args: dict[str, Any]
    risk: str
    nonce: str
    issued_at: str
    expires_at: str
    policy_version: int
    device_id: str
    signature: str = ""

    def signing_payload(self) -> bytes:
        """Everything the signature covers — deliberately *not* the signature itself."""
        return canonical_json(
            {
                "job_id": self.job_id,
                "action": self.action,
                "args": self.args,
                "risk": self.risk,
                "nonce": self.nonce,
                "issued_at": self.issued_at,
                "expires_at": self.expires_at,
                "policy_version": self.policy_version,
                "device_id": self.device_id,
            }
        ).encode()

    def is_expired(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        return datetime.fromisoformat(self.expires_at) <= moment

    def to_wire(self) -> dict[str, Any]:
        return {"type": MessageType.JOB_DISPATCH.value, **asdict(self)}

    @classmethod
    def from_wire(cls, message: dict[str, Any]) -> JobEnvelope:
        fields = {k: v for k, v in message.items() if k != "type"}
        return cls(**fields)


@dataclass(frozen=True)
class JobResult:
    """What the helper reports back. Observations, never a verdict.

    The helper says what it saw; the server's verifier decides whether that satisfies
    what the action required. A device must not be able to declare its own success.
    """

    job_id: str
    status: str  # completed | failed | rejected
    observed: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    reject_reason: str | None = None
    signature: str = ""

    def signing_payload(self) -> bytes:
        return canonical_json(
            {
                "job_id": self.job_id,
                "status": self.status,
                "observed": self.observed,
                "error": self.error,
                "reject_reason": self.reject_reason,
            }
        ).encode()

    def to_wire(self) -> dict[str, Any]:
        return {"type": MessageType.JOB_RESULT.value, **asdict(self)}

    @classmethod
    def from_wire(cls, message: dict[str, Any]) -> JobResult:
        return cls(**{k: v for k, v in message.items() if k != "type"})
