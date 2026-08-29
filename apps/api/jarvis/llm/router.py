"""The LLM router: a cascade of providers, one per call class.

PLAN.md §4. Three ideas, in priority order:

1. **Route by intent, not by model.** ``extract`` goes to whichever provider constrains
   decoding to a schema; ``chat`` goes to whichever answers fastest. Changing that is a
   table edit here, not a change at any call site.
2. **A rate limit is not an outage.** A 429 advances the cascade and trips a breaker for
   that provider alone.
3. **Spend cannot exceed the cap.** Paid providers are filtered out before the attempt,
   not apologised for afterwards.

With ``ENABLE_PAID_LLM=false`` the paid tier is unreachable and the system must still work
end to end on free providers. ``tests/unit/test_llm_router.py`` asserts exactly that.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.config import get_settings
from jarvis.core.correlation import get_correlation_id
from jarvis.core.ids import uuid7
from jarvis.core.logging import get_logger
from jarvis.llm.base import LLMProvider
from jarvis.llm.budget import BudgetGuard
from jarvis.llm.health import ProviderHealthStore
from jarvis.llm.providers import (
    GeminiProvider,
    GroqProvider,
    OllamaProvider,
    OpenRouterFreeProvider,
    OpenRouterPaidProvider,
)
from jarvis.llm.types import (
    AllProvidersFailed,
    CallClass,
    LLMError,
    LLMRequest,
    LLMResponse,
    ProviderNotConfigured,
)

log = get_logger(__name__)

# Provider order per call class. Accuracy leads for EXTRACT; latency leads elsewhere.
DEFAULT_CASCADE: dict[CallClass, tuple[str, ...]] = {
    CallClass.CLASSIFY: ("groq", "gemini", "openrouter_free", "openrouter_paid"),
    CallClass.PLAN: ("groq", "gemini", "openrouter_free", "openrouter_paid"),
    CallClass.REFLECT: ("groq", "gemini", "openrouter_free", "openrouter_paid"),
    CallClass.CHAT: ("groq", "gemini", "openrouter_free"),
    CallClass.EXTRACT: ("gemini", "groq", "openrouter_paid"),
}


def default_providers() -> dict[str, LLMProvider]:
    return {
        p.name: p
        for p in (
            GroqProvider(),
            GeminiProvider(),
            OpenRouterFreeProvider(),
            OpenRouterPaidProvider(),
            OllamaProvider(),
        )
    }


class LLMRouter:
    def __init__(
        self,
        session: AsyncSession,
        *,
        providers: dict[str, LLMProvider] | None = None,
        cascade: dict[CallClass, tuple[str, ...]] | None = None,
    ) -> None:
        self.session = session
        self.providers = providers if providers is not None else default_providers()
        self.cascade = cascade or DEFAULT_CASCADE
        self.health = ProviderHealthStore(session)
        self.budget = BudgetGuard(session)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Run the cascade for this call class, returning the first success.

        Raises ``AllProvidersFailed`` carrying a per-provider reason, so a failure says
        *why* the system could not think rather than merely that it could not.
        """
        order = self.cascade.get(request.call_class, ())
        budget = await self.budget.status()
        cooling = await self.health.cooling_down()
        failures: dict[str, str] = {}
        attempt = 0

        for name in order:
            provider = self.providers.get(name)
            if provider is None:
                failures[name] = "not registered"
                continue

            if skip_reason := self._skip_reason(provider, request, budget, cooling):
                failures[name] = skip_reason
                continue

            attempt += 1
            try:
                response = await provider.generate(request)
            except ProviderNotConfigured as exc:
                # Not a failure: an unconfigured provider was never available.
                failures[name] = str(exc)
                continue
            except LLMError as exc:
                failures[name] = f"{type(exc).__name__}: {exc}"
                await self.health.record_failure(name, str(exc))
                await self._record_call(
                    request, provider, status="error", error=str(exc), attempt=attempt
                )
                log.warning(
                    "llm_cascade_advance",
                    call_class=request.call_class.value,
                    failed_provider=name,
                    reason=str(exc)[:200],
                )
                continue

            response.attempts = attempt
            await self.health.record_success(name)
            await self._record_call(
                request, provider, status="ok", response=response, attempt=attempt
            )
            log.info(
                "llm_call_ok",
                call_class=request.call_class.value,
                provider=name,
                model=response.model,
                latency_ms=response.latency_ms,
                cost_inr=round(response.cost_inr, 4),
                attempt=attempt,
            )
            return response

        log.error(
            "llm_all_providers_failed", call_class=request.call_class.value, failures=failures
        )
        raise AllProvidersFailed(request.call_class, failures)

    # ── the pre-flight filter ──────────────────────────────────────────
    def _skip_reason(  # noqa: ANN001
        self, provider: LLMProvider, request: LLMRequest, budget, cooling
    ) -> str | None:
        if not provider.is_configured():
            return "not configured"
        if not provider.supports(request.call_class):
            return f"does not support {request.call_class.value}"
        if provider.is_paid and not budget.paid_enabled:
            return "paid inference disabled"
        if provider.is_paid and not budget.allows_paid:
            return (
                f"budget exhausted (₹{budget.spent_inr:.2f} of ₹{budget.limit_inr:.2f} used)"
            )
        if (until := cooling.get(provider.name)) is not None:
            return f"circuit open until {until.isoformat()}"
        return None

    async def _record_call(
        self,
        request: LLMRequest,
        provider: LLMProvider,
        *,
        status: str,
        response: LLMResponse | None = None,
        error: str | None = None,
        attempt: int = 1,
    ) -> None:
        """Append to ``llm_calls``. Accounting is not optional — an unrecorded call is
        spend nobody can see."""
        await self.session.execute(
            text("""
                INSERT INTO llm_calls
                    (id, user_id, call_class, provider, model, prompt_version,
                     input_tokens, output_tokens, cost_inr, latency_ms,
                     status, error, attempt, correlation_id, created_at, updated_at)
                VALUES
                    (:id, :user_id, :call_class, :provider, :model, :prompt_version,
                     :input_tokens, :output_tokens, :cost_inr, :latency_ms,
                     :status, :error, :attempt, :correlation_id, now(), now())
            """),
            {
                "id": uuid7(),
                "user_id": request.user_id,
                "call_class": request.call_class.value,
                "provider": provider.name,
                "model": getattr(provider, "model", "unknown"),
                "prompt_version": request.prompt_version,
                "input_tokens": response.input_tokens if response else 0,
                "output_tokens": response.output_tokens if response else 0,
                "cost_inr": response.cost_inr if response else 0.0,
                "latency_ms": response.latency_ms if response else 0,
                "status": status,
                "error": error[:2000] if error else None,
                "attempt": attempt,
                "correlation_id": get_correlation_id(),
            },
        )

    # ── convenience ────────────────────────────────────────────────────
    async def chat(
        self, messages: Sequence, *, user_id: uuid.UUID | None = None, **kwargs
    ) -> LLMResponse:
        return await self.generate(
            LLMRequest(
                call_class=CallClass.CHAT, messages=list(messages), user_id=user_id, **kwargs
            )
        )

    async def extract(
        self, messages: Sequence, schema: dict, *, user_id: uuid.UUID | None = None, **kwargs
    ) -> LLMResponse:
        """Structured extraction. Low temperature by default: creativity is a defect here."""
        kwargs.setdefault("temperature", 0.0)
        s = get_settings()
        kwargs.setdefault("max_tokens", min(2048, s.max_tokens_per_run))
        return await self.generate(
            LLMRequest(
                call_class=CallClass.EXTRACT,
                messages=list(messages),
                json_schema=schema,
                user_id=user_id,
                **kwargs,
            )
        )
