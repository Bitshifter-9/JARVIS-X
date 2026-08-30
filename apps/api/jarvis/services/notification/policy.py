"""When and how loudly to interrupt someone.

Escalation is a ladder, not a broadcast: push, then Telegram, then WhatsApp, then a call.
Each rung is more intrusive than the last, and an acknowledgement stops the climb.

Three limits, all from blueprint §7: quiet hours, a per-day cap, and one escalation per
alert. Without them a stuck workflow becomes a notification storm at 3am, which is how
users disable an assistant permanently.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


class Channel(enum.StrEnum):
    PUSH = "push"
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    CALL = "call"
    ALARM = "alarm"


ESCALATION_ORDER = [Channel.PUSH, Channel.TELEGRAM, Channel.WHATSAPP, Channel.ALARM, Channel.CALL]

# Below this, a deadline is urgent enough to break through quiet hours if the user
# enabled that. Above it, it can wait for morning.
QUIET_HOURS_OVERRIDE_WITHIN = timedelta(hours=2)


class Decision(enum.StrEnum):
    SEND = "send"
    DEFER = "defer"
    SUPPRESS = "suppress"


@dataclass(frozen=True)
class DeliveryPlan:
    decision: Decision
    channel: Channel | None
    reason: str
    deliver_at: datetime | None = None


@dataclass(frozen=True)
class UserPreferences:
    timezone: str = "Asia/Kolkata"
    quiet_hours: str | None = "22:30-07:00"
    max_per_day: int = 20
    max_calls_per_day: int = 3
    allow_quiet_hours_override: bool = False
    enabled_channels: tuple[Channel, ...] = (Channel.PUSH, Channel.TELEGRAM)


def parse_quiet_hours(value: str | None) -> tuple[time, time] | None:
    if not value or "-" not in value:
        return None
    start_text, end_text = value.split("-", 1)
    try:
        start = time.fromisoformat(start_text.strip())
        end = time.fromisoformat(end_text.strip())
    except ValueError:
        return None
    return start, end


def in_quiet_hours(moment: datetime, prefs: UserPreferences) -> bool:
    window = parse_quiet_hours(prefs.quiet_hours)
    if window is None:
        return False
    local = moment.astimezone(ZoneInfo(prefs.timezone)).time()
    start, end = window
    # A window that wraps midnight is the normal case, not the exception.
    return start <= local or local < end if start > end else start <= local < end


def next_allowed_time(moment: datetime, prefs: UserPreferences) -> datetime:
    window = parse_quiet_hours(prefs.quiet_hours)
    if window is None:
        return moment
    zone = ZoneInfo(prefs.timezone)
    local = moment.astimezone(zone)
    resume = local.replace(
        hour=window[1].hour, minute=window[1].minute, second=0, microsecond=0
    )
    if resume <= local:
        resume += timedelta(days=1)
    return resume.astimezone(UTC)


def plan_delivery(
    *,
    now: datetime,
    prefs: UserPreferences,
    attempt: int,
    sent_today: int,
    calls_today: int,
    due_at: datetime | None = None,
    acknowledged: bool = False,
) -> DeliveryPlan:
    """Decide whether this alert goes out, waits, or is dropped."""
    if acknowledged:
        return DeliveryPlan(Decision.SUPPRESS, None, "already acknowledged")

    if sent_today >= prefs.max_per_day:
        return DeliveryPlan(
            Decision.SUPPRESS, None, f"daily cap of {prefs.max_per_day} reached"
        )

    available = [c for c in ESCALATION_ORDER if c in prefs.enabled_channels]
    if not available:
        return DeliveryPlan(Decision.SUPPRESS, None, "no channels enabled")

    if attempt >= len(available):
        # The ladder is finite. Climbing forever is a storm, not persistence.
        return DeliveryPlan(Decision.SUPPRESS, None, "escalation ladder exhausted")

    channel = available[attempt]

    if channel is Channel.CALL and calls_today >= prefs.max_calls_per_day:
        remaining = [c for c in available[attempt:] if c is not Channel.CALL]
        if not remaining:
            return DeliveryPlan(Decision.SUPPRESS, None, "daily call cap reached")
        channel = remaining[0]

    if in_quiet_hours(now, prefs):
        urgent = (
            due_at is not None
            and due_at - now <= QUIET_HOURS_OVERRIDE_WITHIN
            and prefs.allow_quiet_hours_override
        )
        if not urgent:
            return DeliveryPlan(
                Decision.DEFER,
                channel,
                "quiet hours",
                deliver_at=next_allowed_time(now, prefs),
            )

    return DeliveryPlan(Decision.SEND, channel, f"escalation step {attempt + 1}")
