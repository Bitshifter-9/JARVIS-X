"""The spend guard.

PLAN.md §14 puts budget control in code rather than in a spreadsheet. The rule is simple
and absolute: once month-to-date paid spend reaches the cap, paid providers are removed
from every cascade. Free providers are unaffected, so the system degrades in quality
rather than stopping.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class BudgetStatus:
    spent_inr: float
    limit_inr: float
    paid_enabled: bool

    @property
    def remaining_inr(self) -> float:
        return max(0.0, self.limit_inr - self.spent_inr)

    @property
    def allows_paid(self) -> bool:
        return self.paid_enabled and self.spent_inr < self.limit_inr


class BudgetGuard:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def status(self) -> BudgetStatus:
        s = get_settings()
        spent = await self.session.scalar(
            text("""
                SELECT COALESCE(SUM(cost_inr), 0.0) FROM llm_calls
                WHERE created_at >= date_trunc('month', now()) AND status = 'ok'
            """)
        )
        return BudgetStatus(
            spent_inr=float(spent or 0.0),
            limit_inr=s.llm_budget_inr,
            paid_enabled=s.enable_paid_llm,
        )
