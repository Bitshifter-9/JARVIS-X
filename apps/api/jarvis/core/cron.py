"""A five-field cron matcher. Stdlib has none and croniter is one more wheel for
sixty lines: ``minute hour day-of-month month day-of-week`` with ``*``, lists, ranges
and ``*/n`` steps. Day-of-week is 0–6 with Sunday as 0 (7 also means Sunday).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))

PRESETS = {
    "every morning": "0 7 * * *",
    "weekday mornings": "0 8 * * 1-5",
    "every evening": "0 21 * * *",
    "sunday evening": "0 18 * * 0",
    "every hour": "0 * * * *",
}


def _field(spec: str, low: int, high: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
            if step < 1:
                raise ValueError("cron: step must be >= 1")
        if part == "*":
            a, b = low, high
        elif "-" in part:
            a_s, b_s = part.split("-", 1)
            a, b = int(a_s), int(b_s)
        else:
            a = b = int(part)
            if step > 1:  # "5/10" means "from 5 in steps of 10"
                b = high
        if a < low or b > high or a > b:
            raise ValueError(f"cron: {part!r} outside {low}-{high}")
        out.update(range(a, b + 1, step))
    return out


def parse(expr: str) -> tuple[set[int], ...]:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron: need 5 fields — minute hour day month weekday")
    parts[4] = re.sub(r"\b7\b", "0", parts[4])  # a literal 7 is Sunday too
    return tuple(_field(p, lo, hi) for p, (lo, hi) in zip(parts, _RANGES, strict=True))


def matches(expr: str, moment: datetime) -> bool:
    minute, hour, dom, month, dow = parse(expr)
    return (
        moment.minute in minute
        and moment.hour in hour
        and moment.day in dom
        and moment.month in month
        and (moment.weekday() + 1) % 7 in dow  # python: Monday=0 → cron: Sunday=0
    )


def next_run(expr: str, after: datetime) -> datetime:
    """The first minute strictly after ``after`` (in its timezone) that matches."""
    parse(expr)  # fail fast on a bad expression
    moment = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = moment + timedelta(days=366)
    while moment < limit:
        if matches(expr, moment):
            return moment
        # Skip whole days that cannot match — the common case is a daily routine.
        _, _, dom, month, dow = parse(expr)
        day_ok = (
            moment.day in dom and moment.month in month and (moment.weekday() + 1) % 7 in dow
        )
        if not day_ok:
            moment = (moment + timedelta(days=1)).replace(hour=0, minute=0)
        else:
            moment += timedelta(minutes=1)
    raise ValueError("cron: no run within a year")
