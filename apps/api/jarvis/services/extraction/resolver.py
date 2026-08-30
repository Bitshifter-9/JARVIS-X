"""Turning an extracted wall time into a confirmed instant.

Blueprint §7: never schedule from raw extracted text. A relative date resolves against the
*message* timestamp, not today's; the result is persisted as a UTC instant plus its IANA
zone, and every reminder is computed from that pair.

Impossible values are rejected rather than clamped. A deadline in the past is far more
often a parse error than a genuinely overdue task, and inventing a plausible date is the
failure mode that produces confidently wrong alerts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.services.extraction.schema import ExtractedDeadline, ResolvedDeadline

MAX_FUTURE = timedelta(days=730)
PAST_TOLERANCE = timedelta(hours=12)
CONFIRM_BELOW_CONFIDENCE = 0.75


class ResolutionError(ValueError):
    pass


def resolve(
    extracted: ExtractedDeadline,
    *,
    received_at: datetime,
    default_timezone: str,
    now: datetime | None = None,
) -> ResolvedDeadline | None:
    if not extracted.has_deadline or not extracted.due_at_local:
        return None

    moment = now or datetime.now(UTC)
    zone_name = extracted.timezone or default_timezone
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(default_timezone)
        zone_name = default_timezone

    try:
        naive = datetime.fromisoformat(extracted.due_at_local.replace("Z", ""))
    except ValueError as exc:
        raise ResolutionError(f"unparseable due_at_local: {extracted.due_at_local!r}") from exc

    if naive.tzinfo is not None:
        due_utc = naive.astimezone(UTC)
    else:
        due_utc = naive.replace(tzinfo=zone).astimezone(UTC)

    if due_utc < received_at - PAST_TOLERANCE:
        raise ResolutionError(
            f"resolved to {due_utc.isoformat()}, before the message was received"
        )
    if due_utc > moment + MAX_FUTURE:
        raise ResolutionError(f"resolved to {due_utc.isoformat()}, implausibly far ahead")

    needs_confirmation = (
        extracted.confidence < CONFIRM_BELOW_CONFIDENCE or bool(extracted.ambiguity)
    )
    reason = extracted.ambiguity or (
        f"confidence {extracted.confidence:.2f} is below {CONFIRM_BELOW_CONFIDENCE}"
        if extracted.confidence < CONFIRM_BELOW_CONFIDENCE
        else None
    )

    return ResolvedDeadline(
        title=extracted.title or "Untitled",
        due_at=due_utc,
        timezone=zone_name,
        kind=extracted.kind,
        confidence=extracted.confidence,
        evidence_span=extracted.evidence_span,
        estimate_minutes=extracted.estimate_minutes,
        owner=extracted.owner,
        needs_confirmation=needs_confirmation,
        reason=reason,
    )
