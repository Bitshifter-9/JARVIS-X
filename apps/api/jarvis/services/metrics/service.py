"""Measuring the definition-of-done targets.

PLAN.md §15 lists seven numbers. This computes them from what actually happened rather
than from what the code intends, so a claim on the dashboard is a query anyone can re-run.

A metric with no observations reports ``None``, never a flattering default. "We have not
measured this yet" and "this is at 100%" are very different statements.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    value: float | None
    target: float
    unit: str
    sample_size: int
    higher_is_better: bool = True

    @property
    def met(self) -> bool | None:
        if self.value is None:
            return None
        return self.value >= self.target if self.higher_is_better else self.value <= self.target

    @property
    def display(self) -> str:
        if self.value is None:
            return "not yet measured"
        if self.unit == "%":
            return f"{self.value:.1%}"
        if self.unit == "s":
            return f"{self.value:.1f}s"
        if self.unit == "INR":
            return f"₹{self.value:.2f}"
        return f"{self.value:.2f}"


@dataclass(frozen=True)
class Scorecard:
    generated_at: datetime
    window_days: int
    metrics: list[Metric]

    @property
    def measured(self) -> list[Metric]:
        return [m for m in self.metrics if m.value is not None]

    @property
    def failing(self) -> list[Metric]:
        return [m for m in self.metrics if m.met is False]

    @property
    def headline(self) -> str:
        measured = len(self.measured)
        if measured == 0:
            return "Nothing measured yet."
        met = sum(1 for m in self.measured if m.met)
        return f"{met}/{measured} targets met ({len(self.metrics) - measured} not yet measured)."


class MetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def scorecard(
        self, user_id: uuid.UUID | None = None, *, window_days: int = 7
    ) -> Scorecard:
        since = datetime.now(UTC) - timedelta(days=window_days)
        params = {"since": since, "uid": str(user_id) if user_id else None}

        return Scorecard(
            generated_at=datetime.now(UTC),
            window_days=window_days,
            metrics=[
                await self._duplicate_task_rate(params),
                await self._verified_tool_success(params),
                await self._approval_coverage(params),
                await self._event_to_alert_latency(params),
                await self._recovery_correctness(params),
                await self._injection_block_rate(params),
                await self._monthly_cost(),
            ],
        )

    async def _scalar(self, sql: str, params: dict) -> tuple[float | None, int]:
        row = (await self.session.execute(text(sql), params)).mappings().one()
        total = int(row["total"] or 0)
        return (float(row["value"]) if total and row["value"] is not None else None), total

    async def _duplicate_task_rate(self, params) -> Metric:
        value, total = await self._scalar(
            """
            SELECT count(*) AS total,
                   COALESCE(
                     SUM(CASE WHEN c > 1 THEN c - 1 ELSE 0 END)::float
                     / NULLIF(SUM(c), 0), 0) AS value
            FROM (
              SELECT source_id, count(*) AS c
              FROM tasks
              WHERE source_id IS NOT NULL AND created_at >= :since
                AND (CAST(:uid AS uuid) IS NULL OR user_id = CAST(:uid AS uuid))
              GROUP BY source_id
            ) grouped
            """,
            params,
        )
        return Metric(
            "duplicate_task_rate", "Duplicate task rate", value, 0.01, "%", total,
            higher_is_better=False,
        )

    async def _verified_tool_success(self, params) -> Metric:
        value, total = await self._scalar(
            """
            SELECT count(*) AS total,
                   AVG(CASE WHEN verdict = 'verified' THEN 1.0 ELSE 0.0 END) AS value
            FROM evidence
            WHERE created_at >= :since
              AND (CAST(:uid AS uuid) IS NULL OR user_id = CAST(:uid AS uuid))
            """,
            params,
        )
        return Metric("verified_tool_success", "Verified tool success", value, 0.95, "%", total)

    async def _approval_coverage(self, params) -> Metric:
        """Of R2/R3 actions that actually ran, how many had a decided approval.

        Measured over *dispatched* actions only: a proposal that was denied never needed
        one, and counting it would flatter the number.
        """
        value, total = await self._scalar(
            """
            SELECT count(*) AS total,
                   AVG(CASE WHEN ap.decision = 'approved' THEN 1.0 ELSE 0.0 END) AS value
            FROM actions a
            LEFT JOIN approvals ap ON ap.action_id = a.id
            WHERE a.risk IN ('R2', 'R3')
              AND a.status IN ('dispatched', 'succeeded')
              AND a.created_at >= :since
              AND (CAST(:uid AS uuid) IS NULL OR a.user_id = CAST(:uid AS uuid))
            """,
            params,
        )
        return Metric("approval_coverage", "Approval coverage (R2/R3)", value, 1.0, "%", total)

    async def _event_to_alert_latency(self, params) -> Metric:
        value, total = await self._scalar(
            """
            SELECT count(*) AS total,
                   AVG(EXTRACT(EPOCH FROM (j.updated_at - e.created_at))) AS value
            FROM events e
            JOIN jobs j ON j.correlation_id = e.correlation_id
            WHERE e.created_at >= :since
              AND j.status = 'succeeded'
              AND (CAST(:uid AS uuid) IS NULL OR e.user_id = CAST(:uid AS uuid))
            """,
            params,
        )
        return Metric(
            "event_to_alert_latency", "Event to alert", value, 10.0, "s", total,
            higher_is_better=False,
        )

    async def _recovery_correctness(self, params) -> Metric:
        """Failed actions that were retried at most once before stopping.

        The blueprint's rule is "repair once or stop clearly"; a job that burned every
        attempt is the failure this measures.
        """
        value, total = await self._scalar(
            """
            SELECT count(*) AS total,
                   AVG(CASE WHEN attempts <= 2 THEN 1.0 ELSE 0.0 END) AS value
            FROM jobs
            WHERE status IN ('dead_lettered', 'succeeded')
              AND created_at >= :since
              AND (CAST(:uid AS uuid) IS NULL OR user_id = CAST(:uid AS uuid))
            """,
            params,
        )
        return Metric("recovery_correctness", "Bounded recovery", value, 0.95, "%", total)

    async def _injection_block_rate(self, params) -> Metric:
        """Effectful actions proposed from untrusted content that were denied.

        The target is 100%: a single one getting through is a breach, not a bad average.
        """
        value, total = await self._scalar(
            """
            SELECT count(*) AS total,
                   AVG(CASE WHEN detail->>'decision' = 'deny' THEN 1.0 ELSE 0.0 END) AS value
            FROM audit_log
            WHERE action = 'action.proposed'
              AND detail->>'reason' LIKE '%retrieved content%'
              AND created_at >= :since
              AND (CAST(:uid AS uuid) IS NULL OR user_id = CAST(:uid AS uuid))
            """,
            params,
        )
        return Metric("injection_block_rate", "Prompt-injection block", value, 1.0, "%", total)

    async def _monthly_cost(self) -> Metric:
        row = (
            await self.session.execute(
                text("""
                    SELECT count(*) AS total, COALESCE(SUM(cost_inr), 0) AS value
                    FROM llm_calls
                    WHERE created_at >= date_trunc('month', now()) AND status = 'ok'
                """)
            )
        ).mappings().one()
        return Metric(
            "monthly_cost", "LLM spend this month", float(row["value"] or 0.0), 2000.0,
            "INR", int(row["total"] or 0), higher_is_better=False,
        )
