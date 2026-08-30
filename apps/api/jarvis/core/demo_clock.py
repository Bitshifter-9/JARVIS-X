"""The demo clock.

A seven-minute demo cannot wait for a T-24h reminder, and a faked timer would prove
nothing. This compresses wall time while leaving the real scheduler path intact: the same
`schedules` rows, the same worker, the same version guard — only the clock moves faster.

Off by default and refused outside local/demo, because a system whose sense of time can be
changed by a request is not one whose deadlines mean anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jarvis.core.config import get_settings
from jarvis.core.errors import Forbidden


@dataclass
class DemoClock:
    """Maps one real minute onto ``speed`` minutes of apparent time."""

    started_at: datetime
    speed: float = 60.0
    enabled: bool = False

    def now(self) -> datetime:
        real = datetime.now(UTC)
        if not self.enabled:
            return real
        return self.started_at + (real - self.started_at) * self.speed

    def real_delay_for(self, apparent: timedelta) -> timedelta:
        """How long to actually wait for an apparent interval."""
        return apparent / self.speed if self.enabled else apparent

    def describe(self) -> str:
        if not self.enabled:
            return "Real time."
        return (
            f"1 real minute = {self.speed:.0f} apparent minutes. "
            "The scheduler path is unchanged; only the clock moves."
        )


_clock = DemoClock(started_at=datetime.now(UTC))


def get_clock() -> DemoClock:
    return _clock


def now() -> datetime:
    return _clock.now()


def enable(speed: float = 60.0) -> DemoClock:
    settings = get_settings()
    if settings.env not in ("local", "test", "demo"):
        raise Forbidden("The demo clock is not available in this environment")
    if not 1.0 <= speed <= 3600.0:
        raise ValueError("speed must be between 1 and 3600")

    _clock.started_at = datetime.now(UTC)
    _clock.speed = speed
    _clock.enabled = True
    return _clock


def disable() -> DemoClock:
    _clock.enabled = False
    _clock.started_at = datetime.now(UTC)
    return _clock
