"""Failure prediction and recovery planning.

The differentiator (PLAN.md §8): not reminders, but knowing the current plan is
mathematically unlikely to succeed, and saying what to drop.

**Why a lognormal.** Work durations are bounded below by zero and have a long right tail —
tasks overrun far more often and far worse than they underrun. A normal distribution
would assign probability to finishing in negative time. Fitting a lognormal to two points
we already have (a median estimate and a p80) gives a real probability from two numbers a
person can argue with, with no training data and no dependency beyond the standard library.

**Why total work and not just the critical path.** The blueprint says to sum the critical
path, which is correct when tasks run in parallel across a team. One person works
serially, so the binding constraint is the *total* outstanding work; the critical path
sets the floor that no amount of help could beat. We model both and take whichever binds,
via ``concurrency``. For the default solo user, that is total work — and using the critical
path alone would have quietly predicted success on a plan with twenty independent tasks.
"""

from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import NormalDist

from jarvis.services.goal.graph import GraphAnalysis, TaskNode

# The 80th percentile is this multiple of the median estimate. Software estimates are
# optimistic by roughly this much; it is replaced per-user by observed calibration.
P80_FACTOR = 1.4
Z80 = NormalDist().inv_cdf(0.80)  # ≈ 0.8416

# Blueprint §6 thresholds.
CRITICAL_RATIO = 0.65
AT_RISK_RATIO = 1.0
RAISE_CARD_BELOW_PROBABILITY = 0.65

DEFAULT_SAFETY_BUFFER_MINUTES = 30.0


@dataclass(frozen=True)
class RecoveryOption:
    key: str
    title: str
    detail: str
    probability_after: float
    minutes_saved: float
    tasks_affected: list[str] = field(default_factory=list)
    requires_approval: bool = False


@dataclass(frozen=True)
class Prediction:
    computed_at: datetime
    deadline: datetime | None
    available_minutes: float
    p50_remaining_minutes: float
    p80_remaining_minutes: float
    finish_ratio: float
    probability: float
    severity: str
    calibration_multiplier: float
    critical_path: list[uuid.UUID]
    options: list[RecoveryOption]
    explanation: str

    @property
    def needs_attention(self) -> bool:
        return self.severity in ("at_risk", "critical")


def calibration_multiplier(estimate_actual_pairs: list[tuple[float, float]]) -> float:
    """How wrong this user's estimates usually are, as a multiplier.

    The **median** ratio, not the mean: one task that ran ten times over should inform the
    estimate, not dominate it. Below three observations we return 1.0 — inventing a
    personal multiplier from two data points would be noise wearing the costume of insight.
    """
    ratios = [
        actual / estimate
        for estimate, actual in estimate_actual_pairs
        if estimate > 0 and actual >= 0
    ]
    if len(ratios) < 3:
        return 1.0
    # Clamped: a genuinely wild ratio is usually a mistyped estimate, not a slow week.
    return max(0.5, min(4.0, statistics.median(ratios)))


def probability_of_finishing(available_minutes: float, p50: float, p80: float) -> float:
    """P(work fits in the time available), from a lognormal fitted to (p50, p80)."""
    if p50 <= 0:
        return 1.0                      # nothing left to do
    if available_minutes <= 0:
        return 0.0                      # the deadline has passed
    if p80 <= p50:
        # No spread given, so treat the estimate as certain.
        return 1.0 if available_minutes >= p50 else 0.0

    mu = math.log(p50)                  # median of a lognormal is exp(mu)
    sigma = math.log(p80 / p50) / Z80
    return NormalDist().cdf((math.log(available_minutes) - mu) / sigma)


def severity_for(finish_ratio: float) -> str:
    if finish_ratio < CRITICAL_RATIO:
        return "critical"
    if finish_ratio < AT_RISK_RATIO:
        return "at_risk"
    return "on_track"


def effective_remaining(analysis: GraphAnalysis, concurrency: int = 1) -> float:
    """Outstanding work, given how many things can genuinely happen at once.

    With one worker this is the total. With more, it is bounded below by the critical
    path — the chain no amount of help can shorten.
    """
    if concurrency <= 1:
        return analysis.total_remaining_minutes
    return max(analysis.critical_path_minutes, analysis.total_remaining_minutes / concurrency)


def available_minutes_until(
    deadline: datetime,
    now: datetime,
    *,
    calendar_blocked_minutes: float = 0.0,
    safety_buffer_minutes: float = DEFAULT_SAFETY_BUFFER_MINUTES,
) -> float:
    """Usable minutes: wall clock, less immovable commitments, less a safety buffer.

    The buffer is not pessimism for its own sake — it is the context switch into the work,
    and the last-minute submission that always takes longer than expected.
    """
    total = (deadline - now).total_seconds() / 60.0
    return max(0.0, total - calendar_blocked_minutes - safety_buffer_minutes)


def predict(
    *,
    nodes: dict[uuid.UUID, TaskNode],
    analysis: GraphAnalysis,
    deadline: datetime | None,
    now: datetime,
    calendar_blocked_minutes: float = 0.0,
    safety_buffer_minutes: float = DEFAULT_SAFETY_BUFFER_MINUTES,
    calibration: float = 1.0,
    concurrency: int = 1,
    extension_hours: float = 24.0,
) -> Prediction:
    """Produce the forecast and, when it is bad, the ranked ways out."""
    if deadline is None:
        # No deadline means nothing to miss. Say so rather than inventing a number.
        return Prediction(
            computed_at=now,
            deadline=None,
            available_minutes=math.inf,
            p50_remaining_minutes=analysis.total_remaining_minutes,
            p80_remaining_minutes=analysis.total_remaining_minutes * P80_FACTOR,
            finish_ratio=math.inf,
            probability=1.0,
            severity="on_track",
            calibration_multiplier=calibration,
            critical_path=analysis.critical_path,
            options=[],
            explanation="No deadline is set for this goal, so there is nothing to miss.",
        )

    available = available_minutes_until(
        deadline,
        now,
        calendar_blocked_minutes=calendar_blocked_minutes,
        safety_buffer_minutes=safety_buffer_minutes,
    )

    p50 = effective_remaining(analysis, concurrency) * calibration
    p80 = p50 * P80_FACTOR
    probability = probability_of_finishing(available, p50, p80)
    finish_ratio = math.inf if p80 <= 0 else available / p80
    severity = severity_for(finish_ratio)

    options: list[RecoveryOption] = []
    if probability < RAISE_CARD_BELOW_PROBABILITY or severity != "on_track":
        options = _recovery_options(
            nodes=nodes,
            analysis=analysis,
            available=available,
            calibration=calibration,
            concurrency=concurrency,
            extension_hours=extension_hours,
        )

    return Prediction(
        computed_at=now,
        deadline=deadline,
        available_minutes=available,
        p50_remaining_minutes=p50,
        p80_remaining_minutes=p80,
        finish_ratio=finish_ratio,
        probability=probability,
        severity=severity,
        calibration_multiplier=calibration,
        critical_path=analysis.critical_path,
        options=options,
        explanation=explain(
            available=available,
            p80=p80,
            probability=probability,
            options=options,
            severity=severity,
        ),
    )


def _recovery_options(
    *,
    nodes: dict[uuid.UUID, TaskNode],
    analysis: GraphAnalysis,
    available: float,
    calibration: float,
    concurrency: int,
    extension_hours: float,
) -> list[RecoveryOption]:
    """The three moves from blueprint §6, each priced in probability."""
    options: list[RecoveryOption] = []
    critical = set(analysis.critical_path)

    def probability_with(p50_minutes: float, avail: float = available) -> float:
        return probability_of_finishing(avail, p50_minutes, p50_minutes * P80_FACTOR)

    baseline_p50 = effective_remaining(analysis, concurrency) * calibration

    # 1. Cut optional work that is not on the critical path.
    droppable = [
        n for n in nodes.values()
        if n.is_optional and not n.is_done and n.id not in critical and n.outstanding_minutes > 0
    ]
    if droppable:
        saved = sum(n.outstanding_minutes for n in droppable) * calibration
        options.append(
            RecoveryOption(
                key="reduce_scope",
                title=f"Drop {len(droppable)} optional task(s)",
                detail=(
                    "Remove work that is marked optional and is not on the critical path. "
                    "Nothing else in the plan depends on it."
                ),
                probability_after=probability_with(max(0.0, baseline_p50 - saved)),
                minutes_saved=saved,
                tasks_affected=[n.title for n in droppable],
            )
        )

    # 2. Defer the lowest-priority non-critical work past the deadline.
    deferrable = sorted(
        (
            n for n in nodes.values()
            if not n.is_done and n.id not in critical and not n.is_optional
            and n.outstanding_minutes > 0
        ),
        key=lambda n: (n.priority, -n.outstanding_minutes),
    )
    if deferrable:
        # Defer only as much as is needed, cheapest-value-first, rather than everything.
        chosen: list[TaskNode] = []
        saved = 0.0
        for node in deferrable:
            if probability_with(max(0.0, baseline_p50 - saved)) >= 0.85:
                break
            chosen.append(node)
            saved += node.outstanding_minutes * calibration
        if chosen:
            options.append(
                RecoveryOption(
                    key="defer_noncritical",
                    title=f"Postpone {len(chosen)} non-critical task(s)",
                    detail=(
                        "Move lower-priority work that is not on the critical path to after "
                        "the deadline. The goal still completes; less of it ships on time."
                    ),
                    probability_after=probability_with(max(0.0, baseline_p50 - saved)),
                    minutes_saved=saved,
                    tasks_affected=[n.title for n in chosen],
                    requires_approval=True,
                )
            )

    # 3. Ask for more time, or for help.
    extended = available + extension_hours * 60.0
    options.append(
        RecoveryOption(
            key="request_extension",
            title=f"Request a {extension_hours:.0f}-hour extension",
            detail=(
                "Keep the full scope and move the deadline. This is the only option that "
                "does not reduce what you deliver."
            ),
            probability_after=probability_with(baseline_p50, extended),
            minutes_saved=0.0,
            requires_approval=True,
        )
    )
    if concurrency == 1 and analysis.total_remaining_minutes > analysis.critical_path_minutes:
        parallel_p50 = effective_remaining(analysis, 2) * calibration
        options.append(
            RecoveryOption(
                key="request_help",
                title="Bring in a second person",
                detail=(
                    "Split the work that is not on the critical path. The critical path "
                    f"itself is {analysis.critical_path_minutes:.0f} minutes and cannot be "
                    "shortened by adding people."
                ),
                probability_after=probability_with(parallel_p50),
                minutes_saved=baseline_p50 - parallel_p50,
                requires_approval=True,
            )
        )

    # Blueprint §6 ranks "by deadline probability and value". Raw probability alone
    # always crowns "ask for an extension", because moving the deadline makes anything
    # fit — while being the one option the user cannot execute alone. So an option that
    # depends on somebody else is discounted before ranking.
    return sorted(options, key=lambda o: -_option_score(o))


# How much an option is discounted for needing another person's agreement. Large enough
# that a self-service fix wins a close call, small enough that it cannot mask a rescue.
EXTERNAL_DEPENDENCY_DISCOUNT = 0.20

# Below this lift, an option is not worth putting in a headline sentence.
MATERIAL_LIFT = 0.10


def _option_score(option: RecoveryOption) -> float:
    penalty = EXTERNAL_DEPENDENCY_DISCOUNT if option.requires_approval else 0.0
    return option.probability_after - penalty


def explain(
    *,
    available: float,
    p80: float,
    probability: float,
    options: list[RecoveryOption],
    severity: str,
) -> str:
    """The sentence a human reads, generated from these numbers and never hardcoded.

    It must survive being read aloud to a sceptical person, so it states both quantities
    and the arithmetic between them.
    """
    if severity == "on_track":
        return (
            f"You have {available:.0f} usable minutes, and the 80th-percentile remaining "
            f"work is {p80:.0f} minutes. Completion probability is {probability:.0%}."
        )

    head = (
        f"You have {available:.0f} usable minutes, while the 80th-percentile remaining "
        f"work is {p80:.0f} minutes. Completion probability is {probability:.0%}."
    )
    if not options:
        return head

    # Lead with something the user can do unaided, when it genuinely helps. Recommending
    # "ask for an extension" to someone who has not yet dropped their optional work is
    # advice that offloads the decision rather than making one.
    self_service = [
        o for o in options
        if not o.requires_approval and o.probability_after >= probability + MATERIAL_LIFT
    ]
    best = (
        max(self_service, key=lambda o: o.probability_after)
        if self_service
        else max(options, key=lambda o: o.probability_after)
    )

    if best.probability_after <= probability + 0.01:
        return f"{head} No available change materially improves this — the scope needs a decision."

    action = (
        f"Removing {_humanise(best.tasks_affected)}"
        if best.tasks_affected
        else best.title
    )
    return (
        f"{head} {action} raises the predicted completion probability from "
        f"{probability:.0%} to {best.probability_after:.0%}."
    )


def _humanise(titles: list[str], limit: int = 2) -> str:
    shown = titles[:limit]
    rest = len(titles) - len(shown)
    joined = " and ".join(shown) if len(shown) <= 2 else ", ".join(shown[:-1]) + f" and {shown[-1]}"
    return f"{joined} and {rest} other task(s)" if rest > 0 else joined


def schedule_offsets() -> list[tuple[str, timedelta]]:
    """The reminder ladder from blueprint §7."""
    return [
        ("T-24h", timedelta(hours=24)),
        ("T-2h", timedelta(hours=2)),
        ("T-1h", timedelta(hours=1)),
        ("T-15m", timedelta(minutes=15)),
    ]
