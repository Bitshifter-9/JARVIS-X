"""What extraction is allowed to return.

The schema is the containment boundary. It has no field capable of expressing a tool call,
an instruction, or a change of policy — so retrieved content that tries to issue orders
produces, at worst, a badly-titled task.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ExtractedDeadline(BaseModel):
    has_deadline: bool
    title: str | None = Field(default=None, max_length=300)
    due_at_local: str | None = Field(
        default=None, description="ISO 8601 local wall time, e.g. 2026-09-05T23:59"
    )
    timezone: str | None = Field(default=None, max_length=64)
    all_day: bool = False
    estimate_minutes: int | None = Field(default=None, ge=0, le=10_000)
    owner: str | None = Field(default=None, max_length=200)
    kind: Literal["assignment", "exam", "meeting", "payment", "submission", "other"] = "other"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_span: str | None = Field(
        default=None, max_length=500, description="The exact substring the deadline was read from"
    )
    ambiguity: str | None = Field(
        default=None, max_length=300,
        description="Set when more than one reading is plausible; the caller must ask.",
    )


DEADLINE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "has_deadline": {"type": "boolean"},
        "title": {"type": ["string", "null"]},
        "due_at_local": {"type": ["string", "null"]},
        "timezone": {"type": ["string", "null"]},
        "all_day": {"type": "boolean"},
        "estimate_minutes": {"type": ["integer", "null"]},
        "owner": {"type": ["string", "null"]},
        "kind": {
            "type": "string",
            "enum": ["assignment", "exam", "meeting", "payment", "submission", "other"],
        },
        "confidence": {"type": "number"},
        "evidence_span": {"type": ["string", "null"]},
        "ambiguity": {"type": ["string", "null"]},
    },
    "required": ["has_deadline", "confidence"],
}


class ResolvedDeadline(BaseModel):
    """An extraction that survived resolution against a real clock and timezone."""

    title: str
    due_at: datetime
    timezone: str
    kind: str
    confidence: float
    evidence_span: str | None = None
    estimate_minutes: int | None = None
    owner: str | None = None
    needs_confirmation: bool = False
    reason: str | None = None
