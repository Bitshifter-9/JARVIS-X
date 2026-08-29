"""Per-provider circuit breaker.

A provider that just returned 429 will almost certainly return 429 again a second later.
Retrying it on every call wastes the request budget of a *rate-limited* provider — the one
resource that is scarce — and adds latency to every request in the meantime.

State lives in the database rather than in process memory so that the API and the workers
share one view: when the worker discovers Groq is throttled, the API stops trying too.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger

log = get_logger(__name__)


class ProviderHealthStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cooling_down(self) -> dict[str, datetime]:
        """Providers currently in cooldown, mapped to when they become eligible again."""
        rows = (
            await self.session.execute(
                text("""
                    SELECT provider, cooldown_until FROM provider_health
                    WHERE cooldown_until IS NOT NULL AND cooldown_until > now()
                """)
            )
        ).mappings().all()
        return {r["provider"]: r["cooldown_until"] for r in rows}

    async def record_success(self, provider: str) -> None:
        await self.session.execute(
            text("""
                INSERT INTO provider_health
                    (provider, consecutive_failures, cooldown_until, last_success_at,
                     total_calls, total_failures, created_at, updated_at)
                VALUES (:p, 0, NULL, now(), 1, 0, now(), now())
                ON CONFLICT (provider) DO UPDATE SET
                    consecutive_failures = 0,
                    cooldown_until       = NULL,
                    last_success_at      = now(),
                    total_calls          = provider_health.total_calls + 1,
                    updated_at           = now()
            """),
            {"p": provider},
        )

    async def record_failure(self, provider: str, error: str) -> None:
        """Count the failure and open the breaker once the threshold is crossed.

        The cooldown is applied in SQL so two concurrent workers cannot each read
        ``failures = 2`` and both conclude the breaker should stay closed.
        """
        s = get_settings()
        cooldown = timedelta(seconds=s.provider_cooldown_seconds)
        await self.session.execute(
            text("""
                INSERT INTO provider_health
                    (provider, consecutive_failures, last_error, total_calls, total_failures,
                     created_at, updated_at)
                VALUES (:p, 1, :err, 1, 1, now(), now())
                ON CONFLICT (provider) DO UPDATE SET
                    consecutive_failures = provider_health.consecutive_failures + 1,
                    last_error           = :err,
                    total_calls          = provider_health.total_calls + 1,
                    total_failures       = provider_health.total_failures + 1,
                    cooldown_until       = CASE
                        WHEN provider_health.consecutive_failures + 1 >= :threshold
                        THEN now() + make_interval(secs => :cooldown)
                        ELSE provider_health.cooldown_until
                    END,
                    updated_at           = now()
            """),
            {
                "p": provider,
                "err": error[:2000],
                "threshold": s.provider_failure_threshold,
                "cooldown": cooldown.total_seconds(),
            },
        )
        log.warning("llm_provider_failure", provider=provider, error=error[:200])

    async def reset(self, provider: str) -> None:
        """Close the breaker manually — for an operator, or for a test."""
        await self.session.execute(
            text("""
                UPDATE provider_health
                SET consecutive_failures = 0, cooldown_until = NULL, updated_at = now()
                WHERE provider = :p
            """),
            {"p": provider},
        )

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)
